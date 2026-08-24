FROM alpine:3.20

RUN apk add --no-cache bash curl openssl xxd jq python3 tzdata

WORKDIR /app
RUN mkdir -p /app/data

COPY scrape_data.sh /app/scrape_data.sh
RUN chmod +x /app/scrape_data.sh

CMD ["/bin/bash", "/app/scrape_data.sh"]