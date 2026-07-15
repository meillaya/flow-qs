# syntax=docker/dockerfile:1@sha256:87999aa3d42bdc6bea60565083ee17e86d1f3339802f543c0d03998580f9cb89

FROM golang:1.26.5@sha256:d52df9c279840adf958d017ebb275651ed8338b953d39817bc3633a2e6b1bbcc AS builder

WORKDIR /app

COPY go.mod go.sum ./
RUN go mod download

COPY . ./

RUN CGO_ENABLED=0 GOOS=linux go build -ldflags="-s -w" -o payment-gateway .

FROM gcr.io/distroless/static-debian12:nonroot@sha256:aef9602f8710ec12bde19d593fed1f76c708531bb7aba205110f1029786ead7b

WORKDIR /
COPY --from=builder /app/payment-gateway /payment-gateway

USER nonroot:nonroot

EXPOSE 8005 50052

ENTRYPOINT ["/payment-gateway"]
