# syntax=docker/dockerfile:1.7
FROM node:24.7.0-alpine@sha256:be4d5e92ac68483ec71440bf5934865b4b7fcb93588f17a24d411d15f0204e4f AS builder
WORKDIR /build
COPY package.json package-lock.json ./
RUN npm ci --ignore-scripts
COPY . .
RUN npm run build

FROM nginxinc/nginx-unprivileged:1.29.1-alpine@sha256:27985295bdb22a1ef8f712863210bd5877c0f3006494a593e86b3fe0fa55467e
COPY nginx.conf /etc/nginx/conf.d/default.conf
COPY --from=builder /build/dist /usr/share/nginx/html
USER 101:101
EXPOSE 8080

