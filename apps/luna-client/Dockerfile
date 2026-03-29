# Luna Client - PWA Production Dockerfile
# Multi-stage build: Vite build → nginx static serve

# ===== Build Stage =====
FROM node:20-alpine AS builder

WORKDIR /app

ARG VITE_API_BASE_URL=https://luna.agentprovision.com
ENV VITE_API_BASE_URL=$VITE_API_BASE_URL

COPY package.json package-lock.json* ./
RUN npm install --silent

COPY . .
RUN npm run build

# ===== Production Stage =====
FROM nginx:1.25-alpine

LABEL maintainer="ServiceTsunami Team" \
      description="Luna Client - PWA"

RUN apk add --no-cache curl

RUN mkdir -p /var/cache/nginx /var/log/nginx /etc/nginx/conf.d && \
    chown -R nginx:nginx /var/cache/nginx /var/log/nginx /etc/nginx/conf.d && \
    touch /tmp/nginx.pid && \
    chown nginx:nginx /tmp/nginx.pid

RUN rm -rf /etc/nginx/conf.d/default.conf

COPY --from=builder /app/dist /usr/share/nginx/html
COPY nginx.conf /etc/nginx/conf.d/default.conf

RUN chown nginx:nginx /etc/nginx/nginx.conf && \
    sed -i 's/^user/#user/' /etc/nginx/nginx.conf && \
    sed -i 's|/var/run/nginx.pid|/tmp/nginx.pid|g' /etc/nginx/nginx.conf

RUN chown -R nginx:nginx /usr/share/nginx/html

USER nginx

EXPOSE 80

HEALTHCHECK --interval=30s --timeout=10s --start-period=10s --retries=3 \
    CMD curl -f http://localhost:80/ || exit 1

CMD ["nginx", "-g", "daemon off;"]
