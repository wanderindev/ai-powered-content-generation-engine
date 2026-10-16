FROM public.ecr.aws/docker/library/python:3.12-slim

RUN pip install --no-cache-dir 'sentry-sdk>=2.0.0' 'cryptography>=42.0.0'

WORKDIR /app
COPY check_certs.py /app/check_certs.py

# Run on startup, then once per day. `|| true` so a transient failure does not
# kill the loop — the check itself reports the failure to Sentry.
CMD ["sh", "-c", "while :; do python /app/check_certs.py || true; sleep 86400; done"]