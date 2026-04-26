FROM node:22-alpine

WORKDIR /app

COPY package.json package-lock.json ./
RUN npm ci

COPY tsconfig.json server.ts ./
RUN chown -R node:node /app

# Run as the unprivileged `node` user (Trivy DS-0002). The process
# holds LOCKSTEP_0G_PRIVATE_KEY in memory; non-root is cheap defense
# in depth.
USER node

# When containerized, server.ts binds 127.0.0.1 by default which makes
# Docker port-mapping unreachable. Override to 0.0.0.0 inside the
# container; the operator MUST run with `-p 127.0.0.1:7878:7878`
# (NOT bare `-p 7878:7878`, which exposes the wallet to anyone who
# can reach the host's network interfaces). See README.md "Security".
ENV LOCKSTEP_0G_STORAGE_BIND_HOST=0.0.0.0

EXPOSE 7878

CMD ["npx", "tsx", "server.ts"]
