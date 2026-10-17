# Build stage
# Node 24 Active LTS (Vite 8 erfordert Node ^20.19 || >=22.12,
# vite-plugin-pwa 2 erfordert Node >=20.19)
FROM node:24-alpine AS build
WORKDIR /app
COPY package.json package-lock.json* ./
# FE-L4: npm ci statt npm install — reproduzierbarer Build streng nach Lockfile
# (npm install kann das Lockfile mutieren und andere Versionen aufloesen).
RUN npm ci
COPY . .
# Versions-Single-Source-of-Truth: Repo-Root-VERSION ueber den benannten
# Build-Context "repo" (docker-compose: additional_contexts, release.yml:
# build-contexts). vite.config.js liest ../VERSION relativ zu /app.
COPY --from=repo VERSION /VERSION
RUN npm run build

# Production stage
# Konkreter Stable-Tag statt floating "alpine" (reproduzierbar, bewusste Bumps).
# Mindestens 1.29.3: nginx.conf + ssl/nginx-ssl.conf nutzen "add_header_inherit merge".
FROM nginx:1.30-alpine
COPY --from=build /app/dist /usr/share/nginx/html
COPY nginx.conf /etc/nginx/conf.d/default.conf
# Gemeinsame server-Regeln fuer nginx.conf UND ssl/nginx-ssl.conf (include).
COPY nginx-common.conf /etc/nginx/snippets/zettelwirtschaft-common.conf

# nginx als non-root laufen lassen (Defense-in-Depth, N-006).
# - Listen-Port von 80 auf 8080 (privilegierte Ports < 1024 brauchen root)
# - Pfade auf nginx-User umschreiben
RUN sed -i 's/listen\s*80;/listen 8080;/' /etc/nginx/conf.d/default.conf \
 && chown -R nginx:nginx /var/cache/nginx /var/log/nginx /etc/nginx/conf.d /usr/share/nginx/html \
 && touch /var/run/nginx.pid \
 && chown nginx:nginx /var/run/nginx.pid

USER nginx
EXPOSE 8080
CMD ["nginx", "-g", "daemon off;"]
