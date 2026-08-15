FROM node:22-alpine AS build
WORKDIR /app
COPY package.json package-lock.json /app/
RUN npm ci
COPY tsconfig.json /app/tsconfig.json
COPY src /app/src
RUN npm run build

FROM node:22-alpine
WORKDIR /app
ENV NODE_ENV=production
COPY package.json package-lock.json /app/
RUN npm ci --omit=dev
COPY --from=build /app/dist /app/dist
EXPOSE 3000
CMD ["node", "dist/main.js"]
