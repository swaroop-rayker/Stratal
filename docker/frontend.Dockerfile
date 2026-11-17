# Node for the frontend (SPEC §7.4, UI-2.1). Node, like Python, runs only in containers.
# The source is bind-mounted at /app/frontend; node_modules lives in a named volume.
FROM node:24-alpine

# Machine-specific extra CAs (docker/certs/README.md): Norton's root on the owner's
# laptop, nothing elsewhere. Node adds them to its own bundle; verification stays on.
COPY docker/certs/ /usr/local/share/stratal-certs/
RUN cat /usr/local/share/stratal-certs/*.crt > /usr/local/share/stratal-certs/extra.pem 2>/dev/null \
    || touch /usr/local/share/stratal-certs/extra.pem
ENV NODE_EXTRA_CA_CERTS=/usr/local/share/stratal-certs/extra.pem \
    NPM_CONFIG_UPDATE_NOTIFIER=false \
    NPM_CONFIG_FUND=false

WORKDIR /app/frontend
EXPOSE 5173
CMD ["npm", "run", "dev"]
