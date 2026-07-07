FROM golang:1.25 AS build

WORKDIR /app

COPY go.mod go.sum ./
RUN go mod download

COPY . .
RUN CGO_ENABLED=0 GOOS=linux go build -o /commerce ./cmd/server

FROM gcr.io/distroless/static-debian12

COPY --from=build /commerce /commerce

ENV PORT=8080
EXPOSE 8080

ENTRYPOINT ["/commerce"]
