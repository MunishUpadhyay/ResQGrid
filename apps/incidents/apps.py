from django.apps import AppConfig


class IncidentsConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "apps.incidents"
    label = "incidents"

    def ready(self):
        import logging

        logger = logging.getLogger(__name__)

        from apps.agents.agents import SentinelAgent

        original_sentinel_run = SentinelAgent.run

        def custom_sentinel_run(self, signal) -> dict:
            logger.info(
                "[SentinelAgent Monkeypatch] Running domain classification on signal %s",
                getattr(signal, 'id', 'mock_id')
            )
            result = original_sentinel_run(self, signal)

            valid_domains = {"legal", "health", "emergency", "civic", "cross_domain"}
            domain = result.get("domain")
            if domain not in valid_domains:
                logger.warning(
                    "[SentinelAgent Monkeypatch] Normalizing invalid domain: %s",
                    domain
                )
                if not domain:
                    domain = "cross_domain"
                elif "|" in domain or "and" in domain or "cross" in domain:
                    domain = "cross_domain"
                elif "medical" in domain or "hospital" in domain:
                    domain = "health"
                else:
                    domain = "cross_domain"
                result["domain"] = domain

            return result

        SentinelAgent.run = custom_sentinel_run
