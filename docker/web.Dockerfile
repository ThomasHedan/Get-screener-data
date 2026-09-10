# The dashboard. Reads the extractor's volume read-only; never writes anything.
FROM node:22-slim AS build
WORKDIR /app
COPY package.json package-lock.json ./
RUN npm ci
COPY . .
RUN npm run build

# next.config.mjs sets output:"standalone", so this stage carries the server and
# its resolved dependencies only -- no node_modules tree, no source, no toolchain.
FROM node:22-slim
WORKDIR /app
ENV NODE_ENV=production \
    SCREENER_DATA_DIR=/data \
    PORT=3000 \
    HOSTNAME=0.0.0.0

COPY --from=build /app/.next/standalone ./
COPY --from=build /app/.next/static ./.next/static

USER node
EXPOSE 3000
CMD ["node", "server.js"]
