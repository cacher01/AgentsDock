FROM golang:1.26-alpine AS build
WORKDIR /src
COPY go.mod go.sum ./
RUN go mod download
COPY . .
RUN CGO_ENABLED=0 go build -trimpath -ldflags="-s -w" -o /out/gateway ./cmd/gateway && CGO_ENABLED=0 go build -trimpath -ldflags="-s -w" -o /out/bootstrap ./cmd/bootstrap

FROM gcr.io/distroless/static-debian12:nonroot
COPY --from=build /out/gateway /gateway
COPY --from=build /out/bootstrap /bootstrap
EXPOSE 8080
USER nonroot:nonroot
ENTRYPOINT ["/gateway"]
