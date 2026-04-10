# Use the official Rust image as a builder
FROM rust:1.75-slim as builder

# Install build dependencies
RUN apt-get update && apt-get install -p pkg-config libssl-dev protobuf-compiler libprotobuf-dev -y && rm -rf /var/lib/apt/lists/*

WORKDIR /usr/src/embedding-service
COPY . .

# Build the application
RUN cargo build --release

# Final runtime image
FROM debian:bookworm-slim

# Install runtime dependencies
RUN apt-get update && apt-get install -y openssl ca-certificates && rm -rf /var/lib/apt/lists/*

WORKDIR /usr/local/bin
COPY --from=builder /usr/src/embedding-service/target/release/embedding-service .

# Expose gRPC port
EXPOSE 50051

CMD ["./embedding-service"]
