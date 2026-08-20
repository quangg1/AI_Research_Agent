FROM node:22-alpine AS build
WORKDIR /app

# Monorepo root context so @kiln/contracts resolves.
COPY apps/web/package.json apps/web/package-lock.json ./apps/web/
COPY packages/contracts ./packages/contracts
WORKDIR /app/apps/web
RUN npm ci || npm install

COPY apps/web/index.html apps/web/tsconfig.json apps/web/vite.config.ts ./
COPY apps/web/src ./src

ARG VITE_API_URL=
ENV VITE_API_URL=$VITE_API_URL
ARG VITE_AUTH_MODE=dev
ENV VITE_AUTH_MODE=$VITE_AUTH_MODE
ARG VITE_CLERK_PUBLISHABLE_KEY=
ENV VITE_CLERK_PUBLISHABLE_KEY=$VITE_CLERK_PUBLISHABLE_KEY
RUN npm run build

FROM nginx:1.27-alpine
COPY --from=build /app/apps/web/dist /usr/share/nginx/html
COPY apps/web/nginx.conf.template /etc/nginx/conf.d/default.conf
EXPOSE 80
CMD ["nginx", "-g", "daemon off;"]
