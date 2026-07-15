# syntax=docker/dockerfile:1

FROM golang:1.26.5 AS builder

WORKDIR /app

COPY go.mod go.sum ./
RUN go mod download

COPY . ./

RUN CGO_ENABLED=0 GOOS=linux go build -ldflags="-s -w" -o payment-gateway .

FROM gcr.io/distroless/static-debian12:nonroot

WORKDIR /
COPY --from=builder /app/payment-gateway /payment-gateway

USER nonroot:nonroot

EXPOSE 8005 50052

ENTRYPOINT ["/payment-gateway"]
