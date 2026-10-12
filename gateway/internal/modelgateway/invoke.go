package modelgateway

import (
	"bytes"
	"context"
	"encoding/json"
	"errors"
	"fmt"
	"io"
	"math"
	"net/http"
	"sort"
	"strings"
	"time"

	"agentplatform/gateway/internal/budget"
)

type Message struct {
	Role    string `json:"role"`
	Content string `json:"content"`
}

type InvokeRequest struct {
	Messages       []Message `json:"messages"`
	ModelVariantID string    `json:"model_variant_id,omitempty"`
	Temperature    *float64  `json:"temperature,omitempty"`
	MaxTokens      *int      `json:"max_tokens,omitempty"`
	Purpose        string    `json:"purpose,omitempty"`
	TaskID         string    `json:"task_id,omitempty"`
	AgentID        string    `json:"agent_id,omitempty"`
}

type InvokeResponse struct {
	Content        string   `json:"content"`
	ModelVariantID string   `json:"model_variant_id"`
	ModelName      string   `json:"model_name"`
	ProviderType   string   `json:"provider_type"`
	RequestTokens  int      `json:"request_tokens"`
	ResponseTokens int      `json:"response_tokens"`
	Cost           float64  `json:"cost"`
	LatencyMS      int64    `json:"latency_ms"`
	FallbackFrom   string   `json:"fallback_from,omitempty"`
	Warnings       []string `json:"warnings,omitempty"`
}

type budgetState struct {
	Policy     budget.Policy
	TaskTokens int64
	TaskCost   float64
	DailyCost  float64
}

func (state budgetState) pressure(addTokens int, addCost float64) float64 {
	tokenPressure := float64(state.TaskTokens+int64(addTokens)) /
		float64(state.Policy.TaskTokenSoftLimit)
	taskCostPressure := (state.TaskCost + addCost) / state.Policy.TaskCostSoftLimit
	dailyCostPressure := (state.DailyCost + addCost) / state.Policy.DailyCostSoftLimit
	return math.Max(tokenPressure, math.Max(taskCostPressure, dailyCostPressure))
}

func (s *Store) Invoke(
	ctx context.Context, workspaceID string, request InvokeRequest,
) (InvokeResponse, error) {
	if len(request.Messages) == 0 {
		return InvokeResponse{}, errors.New("messages are required")
	}
	candidates, err := s.candidates(ctx, workspaceID, request.ModelVariantID)
	if err != nil {
		return InvokeResponse{}, err
	}
	if len(candidates) == 0 {
		return InvokeResponse{}, errors.New("no enabled model is configured")
	}

	originalPrimaryID := candidates[0].VariantID
	var state *budgetState
	var policyWarnings []string
	if request.TaskID != "" {
		loaded, loadErr := s.loadBudgetState(ctx, workspaceID, request.TaskID)
		if loadErr == nil {
			state = &loaded
			preCallPressure := loaded.pressure(0, 0)
			policyWarnings = budgetWarnings(preCallPressure)
			if request.ModelVariantID == "" && preCallPressure >= 0.9 {
				sort.SliceStable(candidates, func(i, j int) bool {
					return modelPriceScore(candidates[i]) < modelPriceScore(candidates[j])
				})
			}
		} else {
			policyWarnings = append(
				policyWarnings,
				"soft budget evaluation was unavailable; normal model selection continued",
			)
		}
	}
	plannedPrimaryID := candidates[0].VariantID
	if plannedPrimaryID != originalPrimaryID {
		policyWarnings = append(
			policyWarnings,
			"soft budget is near its limit; a lower-cost model was preferred",
		)
	}

	var failures []string
	for _, candidate := range candidates {
		response, invokeErr := s.invokeCandidate(ctx, candidate, request)
		if invokeErr == nil {
			if candidate.VariantID != plannedPrimaryID {
				response.FallbackFrom = plannedPrimaryID
				response.Warnings = append(
					response.Warnings,
					"primary model failed; fallback model was used",
				)
			}
			response.Warnings = appendUnique(response.Warnings, policyWarnings...)
			if state != nil {
				projected := state.pressure(
					response.RequestTokens+response.ResponseTokens,
					response.Cost,
				)
				response.Warnings = appendUnique(
					response.Warnings,
					budgetWarnings(projected)...,
				)
			}
			if err := s.recordCall(ctx, workspaceID, request, response); err != nil {
				return InvokeResponse{}, fmt.Errorf(
					"model call fact recording failed: %w", err,
				)
			}
			return response, nil
		}
		failures = append(failures, candidate.VariantID+": "+invokeErr.Error())
	}
	return InvokeResponse{}, fmt.Errorf("all model candidates failed: %s", strings.Join(failures, "; "))
}

func (s *Store) loadBudgetState(
	ctx context.Context, workspaceID, taskID string,
) (budgetState, error) {
	policy, err := budget.NewStore(s.db).Get(ctx, workspaceID)
	if err != nil {
		return budgetState{}, err
	}
	state := budgetState{Policy: policy}
	err = s.db.QueryRow(ctx, `
		select coalesce(sum(c.request_tokens + c.response_tokens), 0),
		       coalesce(sum(c.cost), 0)
		from tasks t
		left join llm_calls c on c.task_id=t.id
		where t.id=$1 and t.workspace_id=$2
		group by t.id
	`, taskID, workspaceID).Scan(&state.TaskTokens, &state.TaskCost)
	if err != nil {
		return budgetState{}, err
	}
	err = s.db.QueryRow(ctx, `
		select coalesce(sum(c.cost), 0)
		from llm_calls c
		join tasks t on t.id=c.task_id
		where t.workspace_id=$1
		  and c.created_at >= date_trunc('day', now())
	`, workspaceID).Scan(&state.DailyCost)
	return state, err
}

func budgetWarnings(pressure float64) []string {
	if pressure >= 1 {
		return []string{
			"soft budget was exceeded; final synthesis remains allowed",
		}
	}
	if pressure >= 0.9 {
		return []string{
			"soft budget is near its limit; a lower-cost model may be preferred",
		}
	}
	return nil
}

func modelPriceScore(model selectedModel) float64 {
	input, inputOK := model.Pricing["input_per_million"].(float64)
	output, outputOK := model.Pricing["output_per_million"].(float64)
	if !inputOK || !outputOK {
		return math.MaxFloat64
	}
	return input + output
}

func appendUnique(current []string, additions ...string) []string {
	seen := make(map[string]struct{}, len(current)+len(additions))
	for _, item := range current {
		seen[item] = struct{}{}
	}
	for _, item := range additions {
		if _, ok := seen[item]; ok {
			continue
		}
		current = append(current, item)
		seen[item] = struct{}{}
	}
	return current
}

func (s *Store) invokeCandidate(
	ctx context.Context, model selectedModel, request InvokeRequest,
) (InvokeResponse, error) {
	key, err := s.cipher.Decrypt(model.Ciphertext, model.Nonce)
	if err != nil {
		return InvokeResponse{}, errors.New("model credential decrypt failed")
	}
	payload := map[string]any{
		"model":    model.ModelName,
		"messages": request.Messages,
	}
	if request.Temperature != nil {
		payload["temperature"] = *request.Temperature
	}
	if request.MaxTokens != nil {
		payload["max_tokens"] = *request.MaxTokens
	}
	body, _ := json.Marshal(payload)
	providerRequest, err := http.NewRequestWithContext(
		ctx,
		http.MethodPost,
		strings.TrimRight(model.BaseURL, "/")+"/chat/completions",
		bytes.NewReader(body),
	)
	if err != nil {
		return InvokeResponse{}, err
	}
	providerRequest.Header.Set("Authorization", "Bearer "+string(key))
	providerRequest.Header.Set("Content-Type", "application/json")
	client := &http.Client{Timeout: 120 * time.Second}
	start := time.Now()
	response, err := client.Do(providerRequest)
	if err != nil {
		return InvokeResponse{}, err
	}
	defer response.Body.Close()
	raw, err := io.ReadAll(io.LimitReader(response.Body, 8<<20))
	if err != nil {
		return InvokeResponse{}, err
	}
	if response.StatusCode < 200 || response.StatusCode >= 300 {
		return InvokeResponse{}, fmt.Errorf("provider status %d", response.StatusCode)
	}
	var decoded struct {
		Choices []struct {
			Message Message `json:"message"`
		} `json:"choices"`
		Usage struct {
			PromptTokens     int `json:"prompt_tokens"`
			CompletionTokens int `json:"completion_tokens"`
		} `json:"usage"`
	}
	if err := json.Unmarshal(raw, &decoded); err != nil || len(decoded.Choices) == 0 {
		return InvokeResponse{}, errors.New("invalid provider response")
	}
	return InvokeResponse{
		Content:        decoded.Choices[0].Message.Content,
		ModelVariantID: model.VariantID,
		ModelName:      model.ModelName,
		ProviderType:   model.ProviderType,
		RequestTokens:  decoded.Usage.PromptTokens,
		ResponseTokens: decoded.Usage.CompletionTokens,
		Cost:           calculateCost(model.Pricing, decoded.Usage.PromptTokens, decoded.Usage.CompletionTokens),
		LatencyMS:      time.Since(start).Milliseconds(),
	}, nil
}

func calculateCost(pricing map[string]any, inputTokens, outputTokens int) float64 {
	inputPrice, _ := pricing["input_per_million"].(float64)
	outputPrice, _ := pricing["output_per_million"].(float64)
	return float64(inputTokens)*inputPrice/1_000_000 +
		float64(outputTokens)*outputPrice/1_000_000
}

func (s *Store) recordCall(
	ctx context.Context,
	workspaceID string,
	request InvokeRequest,
	response InvokeResponse,
) error {
	if request.TaskID == "" {
		return nil
	}
	var fallback any
	if response.FallbackFrom != "" {
		fallback = response.FallbackFrom
	}
	tag, err := s.db.Exec(ctx, `
		insert into llm_calls (
			id, task_id, agent_run_id, model_variant_id,
			request_tokens, response_tokens, cost, latency_ms,
			status, fallback_from
		)
		select $1, t.id, null, $3, $4, $5, $6, $7, 'completed', $8
		from tasks t
		where t.id=$2 and t.workspace_id=$9
	`, "llm_call_"+randomID(), request.TaskID, response.ModelVariantID,
		response.RequestTokens, response.ResponseTokens, response.Cost,
		response.LatencyMS, fallback, workspaceID)
	if err != nil {
		return err
	}
	if tag.RowsAffected() != 1 {
		return errors.New("task not found in workspace")
	}
	return nil
}
