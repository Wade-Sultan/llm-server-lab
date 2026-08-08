# Stage 0: install deps and build
FROM node:24-alpine AS build-stage

WORKDIR /app

COPY package*.json ./
RUN npm ci

COPY ./ ./

ARG NEXT_PUBLIC_API_URL
ENV NEXT_PUBLIC_API_URL=$NEXT_PUBLIC_API_URL

ARG NEXT_PUBLIC_COMMERCE_URL
ENV NEXT_PUBLIC_COMMERCE_URL=$NEXT_PUBLIC_COMMERCE_URL

ARG NEXT_PUBLIC_SECRET_MESSAGE_0
ENV NEXT_PUBLIC_SECRET_MESSAGE_0=$NEXT_PUBLIC_SECRET_MESSAGE_0

ARG NEXT_PUBLIC_SECRET_MESSAGE_1
ENV NEXT_PUBLIC_SECRET_MESSAGE_1=$NEXT_PUBLIC_SECRET_MESSAGE_1

RUN npm run build

# Stage 1: run
FROM node:24-alpine

WORKDIR /app

ENV NODE_ENV=production

COPY --from=build-stage /app/package*.json ./
COPY --from=build-stage /app/dist ./dist
COPY --from=build-stage /app/public ./public
COPY --from=build-stage /app/node_modules ./node_modules
COPY --from=build-stage /app/next.config.mjs ./

EXPOSE 3000
CMD ["node_modules/.bin/next", "start"]

