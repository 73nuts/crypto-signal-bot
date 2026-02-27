# Nginx Configuration

This nginx config is used with the `policy` Docker Compose profile.

It reverse-proxies the Trump policy webhook endpoints to the internal
policy service, handling SSL termination and request routing.

## Usage

Only needed when running the `policy` profile:

```bash
docker-compose --profile policy up
```

Without the `policy` profile, this nginx instance is not started and
the policy webhook endpoints are unavailable.
