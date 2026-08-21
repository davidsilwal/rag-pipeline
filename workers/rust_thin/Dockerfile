# ---- builder: musl static binary (no glibc, no openssl) ---------------------
# Pin the tag for reproducible builds (e.g. rust:1.85-alpine3.20).
FROM rust:alpine AS builder
RUN apk add --no-cache musl-dev
WORKDIR /build
COPY Cargo.toml ./
COPY src ./src
# Compile on alpine → musl target by default; result is a fully static binary.
RUN cargo build --release

# ---- runtime: minimal alpine -------------------------------------------------
FROM alpine:3.20
RUN apk add --no-cache ca-certificates tzdata && \
    adduser -D -H -u 10001 thin
COPY --from=builder /build/target/release/rust-thin-worker /usr/local/bin/rust-thin-worker
USER thin
ENTRYPOINT ["/usr/local/bin/rust-thin-worker"]
