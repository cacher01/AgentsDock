package modelgateway

import (
	"math"
	"testing"

	"agentplatform/gateway/internal/budget"
)

func TestCalculateCost(t *testing.T) {
	cost := calculateCost(
		map[string]any{
			"input_per_million":  2.0,
			"output_per_million": 8.0,
		},
		1000,
		500,
	)
	if cost != 0.006 {
		t.Fatalf("unexpected cost: %f", cost)
	}
}

func TestBudgetPressureUsesHighestLimitRatio(t *testing.T) {
	state := budgetState{
		Policy: budget.Policy{
			TaskTokenSoftLimit: 100,
			TaskCostSoftLimit:  2,
			DailyCostSoftLimit: 10,
		},
		TaskTokens: 80,
		TaskCost:   1,
		DailyCost:  8.5,
	}
	if got := state.pressure(5, 0.6); math.Abs(got-0.91) > 0.000001 {
		t.Fatalf("unexpected pressure: %f", got)
	}
}

func TestBudgetWarningsPreserveSoftCompletion(t *testing.T) {
	if got := budgetWarnings(0.89); len(got) != 0 {
		t.Fatalf("unexpected warning below threshold: %v", got)
	}
	if got := budgetWarnings(0.95); len(got) != 1 {
		t.Fatalf("near-limit warning missing: %v", got)
	}
	got := budgetWarnings(1.01)
	if len(got) != 1 ||
		got[0] != "soft budget was exceeded; final synthesis remains allowed" {
		t.Fatalf("unexpected exceeded warning: %v", got)
	}
}

func TestModelPriceScoreTreatsUnknownPricingAsMostExpensive(t *testing.T) {
	cheap := selectedModel{Pricing: map[string]any{
		"input_per_million": 1.0, "output_per_million": 2.0,
	}}
	if modelPriceScore(cheap) != 3 {
		t.Fatalf("unexpected known price score: %f", modelPriceScore(cheap))
	}
	if modelPriceScore(selectedModel{Pricing: map[string]any{}}) != math.MaxFloat64 {
		t.Fatal("unknown pricing must not be preferred by soft-budget routing")
	}
}

func TestAppendUniqueWarnings(t *testing.T) {
	got := appendUnique([]string{"a"}, "a", "b")
	if len(got) != 2 || got[0] != "a" || got[1] != "b" {
		t.Fatalf("unexpected warnings: %v", got)
	}
}
