# Consola web: build de Vite y nginx sirviendo la SPA. /api se redirige al contenedor de la API,
# así navegador, API y simulador de Webpay comparten origen (sin CORS).
FROM node:22-alpine AS build
WORKDIR /app
COPY package.json package-lock.json ./
RUN npm ci
COPY . .
RUN npm run build

FROM nginx:1.27-alpine
COPY nginx.conf /etc/nginx/conf.d/default.conf
COPY --from=build /app/dist /usr/share/nginx/html
EXPOSE 80
