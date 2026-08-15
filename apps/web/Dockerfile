FROM node:22-alpine AS build
WORKDIR /app
COPY package.json package-lock.json ./
RUN npm ci
COPY index.html tsconfig.json vite.config.ts ./
COPY src ./src
ARG VITE_API_URL=
ENV VITE_API_URL=$VITE_API_URL
RUN npm run build

FROM nginx:1.27-alpine
COPY --from=build /app/dist /usr/share/nginx/html
COPY nginx.conf.template /etc/nginx/templates/kiln.conf.template
EXPOSE 80
CMD ["/bin/sh", "-c", "envsubst '$API_SHARED_KEY' < /etc/nginx/templates/kiln.conf.template > /etc/nginx/conf.d/default.conf && exec nginx -g 'daemon off;'"]
