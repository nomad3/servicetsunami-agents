# Use latest Rust on bookworm to match the runtime image's GLIBC version
FROM rust:slim-bookworm AS builder

# Install build dependencies
RUN apt-get update && apt-get install -y pkg-config libssl-dev protobuf-compiler libprotobuf-dev g++ && rm -rf /var/lib/apt/lists/*

WORKDIR /usr/src/memory-core
COPY . .

# Build the application
RUN cargo build --release

# Final runtime image
FROM debian:bookworm-slim

# Install runtime dependencies
RUN apt-get update && apt-get install -y openssl ca-certificates libpq5 && rm -rf /var/lib/apt/lists/*

WORKDIR /usr/local/bin
COPY --from=builder /usr/src/memory-core/target/release/memory-core .

# Expose gRPC port
EXPOSE 50052

CMD ["./memory-core"]
