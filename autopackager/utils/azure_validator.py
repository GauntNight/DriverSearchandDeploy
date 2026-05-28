"""Azure Configuration and Connectivity Validator"""

import re
from dataclasses import dataclass, field
from typing import List, Optional

import requests
from msal import ConfidentialClientApplication

from autopackager.utils.config import get_config
from autopackager.utils.logger import get_logger

logger = get_logger(__name__)


def _redact(value: str, visible_chars: int = 4) -> str:
    """Redact a secret, showing only the last few characters."""
    if not value or len(value) <= visible_chars:
        return "***"
    return f"***{value[-visible_chars:]}"


@dataclass
class ValidationResult:
    """Result of a single validation check."""
    check_name: str
    passed: bool
    message: str
    details: Optional[str] = None


class AzureConfigurationError(Exception):
    """Raised when Azure configuration validation fails."""

    def __init__(self, results: List[ValidationResult]):
        self.results = results
        failed = [r for r in results if not r.passed]
        lines = ["Azure configuration validation failed:"]
        for r in failed:
            lines.append(f"  - [{r.check_name}] {r.message}")
            if r.details:
                lines.append(f"    Details: {r.details}")
        self.readable_message = "\n".join(lines)
        super().__init__(self.readable_message)


class AzureValidator:
    """Validates Azure/Intune configuration, authentication, and connectivity."""

    def __init__(self):
        config = get_config()
        self.intune_config = config.get("intune", {})
        self.deployment_rings = config.get("deployment_rings", [])

        self.tenant_id = self.intune_config.get("tenant_id", "")
        self.client_id = self.intune_config.get("client_id", "")
        self.client_secret = self.intune_config.get("client_secret", "")
        self.graph_endpoint = self.intune_config.get("graph_endpoint", "https://graph.microsoft.com")
        self.api_version = self.intune_config.get("graph_api_version", "v1.0")

        self.authority = f"https://login.microsoftonline.com/{self.tenant_id}"
        self.scope = ["https://graph.microsoft.com/.default"]

        self._access_token: Optional[str] = None

    def validate_config(self) -> ValidationResult:
        """Validate that required configuration fields are present and not placeholders."""
        missing = []
        for field_name in ("tenant_id", "client_id", "client_secret"):
            value = getattr(self, field_name, "")
            if not value or not value.strip():
                missing.append(field_name)
            elif value.startswith("${"):
                missing.append(field_name)
            elif re.match(r'^your_\w+_here$', value, re.IGNORECASE):
                missing.append(field_name)
            elif re.match(r'^<.+>$', value):
                missing.append(field_name)
            elif re.match(r'^TODO', value, re.IGNORECASE):
                missing.append(field_name)
            elif re.match(r'^CHANGE_ME$', value, re.IGNORECASE):
                missing.append(field_name)

        if missing:
            return ValidationResult(
                check_name="config",
                passed=False,
                message=f"Missing or placeholder config fields: {', '.join(missing)}",
                details="Ensure intune.tenant_id, intune.client_id, and intune.client_secret are set",
            )

        logger.info(
            "Azure config validated",
            tenant_id=_redact(self.tenant_id),
            client_id=_redact(self.client_id),
        )
        return ValidationResult(
            check_name="config",
            passed=True,
            message="All required Azure configuration fields are present",
        )

    def validate_authentication(self) -> ValidationResult:
        """Authenticate to Azure AD using MSAL and obtain an access token."""
        try:
            app = ConfidentialClientApplication(
                self.client_id,
                authority=self.authority,
                client_credential=self.client_secret,
            )
            result = app.acquire_token_for_client(scopes=self.scope)

            if "access_token" in result:
                self._access_token = result["access_token"]
                logger.info("Azure authentication succeeded")
                return ValidationResult(
                    check_name="authentication",
                    passed=True,
                    message="Successfully authenticated to Azure AD",
                )
            else:
                error = result.get("error_description", result.get("error", "Unknown error"))
                logger.error("Azure authentication failed", error=error)
                return ValidationResult(
                    check_name="authentication",
                    passed=False,
                    message="Authentication failed",
                    details=error,
                )
        except Exception as exc:
            logger.error("Azure authentication error", error=str(exc))
            return ValidationResult(
                check_name="authentication",
                passed=False,
                message="Authentication error",
                details=str(exc),
            )

    def validate_graph_access(self) -> ValidationResult:
        """Verify Graph API access by hitting an endpoint the App Reg actually needs.

        Uses /deviceAppManagement/mobileApps because that is the primary surface
        AutoPackager publishes against; reading it requires
        DeviceManagementApps.ReadWrite.All, which the installer grants. The
        previous probe (/organization) needed Organization.Read.All, an
        unrelated permission, and produced false-negative 403s on correctly
        configured tenants.
        """
        if not self._access_token:
            return ValidationResult(
                check_name="graph_access",
                passed=False,
                message="No access token available; authenticate first",
            )

        try:
            url = f"{self.graph_endpoint}/{self.api_version}/deviceAppManagement/mobileApps?$top=1"
            headers = {
                "Authorization": f"Bearer {self._access_token}",
                "Content-Type": "application/json",
            }
            response = requests.get(url, headers=headers, timeout=30)

            if response.status_code == 200:
                logger.info("Graph API access verified")
                return ValidationResult(
                    check_name="graph_access",
                    passed=True,
                    message="Successfully accessed Microsoft Graph API",
                )
            else:
                try:
                    error_body = response.json()
                except Exception:
                    error_body = response.text
                logger.error("Graph API access failed", status_code=response.status_code)
                return ValidationResult(
                    check_name="graph_access",
                    passed=False,
                    message=f"Graph API returned HTTP {response.status_code}",
                    details=str(error_body),
                )
        except Exception as exc:
            logger.error("Graph API connectivity error", error=str(exc))
            return ValidationResult(
                check_name="graph_access",
                passed=False,
                message="Failed to connect to Graph API",
                details=str(exc),
            )

    def validate_deployment_rings(self) -> ValidationResult:
        """Validate that deployment rings are configured with valid Entra group IDs."""
        if not self.deployment_rings:
            return ValidationResult(
                check_name="deployment_rings",
                passed=False,
                message="No deployment rings configured",
                details="Add deployment_rings to config.yaml",
            )

        invalid_rings = []
        for ring in self.deployment_rings:
            name = ring.get("name", "unknown")
            group_id = ring.get("entra_group_id", "")
            if not group_id or group_id.startswith("${") or not group_id.strip():
                invalid_rings.append(name)

        if invalid_rings:
            return ValidationResult(
                check_name="deployment_rings",
                passed=False,
                message=f"Rings with missing/placeholder group IDs: {', '.join(invalid_rings)}",
                details="Set entra_group_id for each deployment ring",
            )

        logger.info("Deployment rings validated", ring_count=len(self.deployment_rings))
        return ValidationResult(
            check_name="deployment_rings",
            passed=True,
            message=f"All {len(self.deployment_rings)} deployment rings are configured",
        )

    def validate_all(self) -> List[ValidationResult]:
        """Run all validation checks and return results.

        Raises:
            AzureConfigurationError: If any validation check fails.
        """
        results: List[ValidationResult] = []

        results.append(self.validate_config())
        results.append(self.validate_authentication())
        results.append(self.validate_graph_access())
        results.append(self.validate_deployment_rings())

        failed = [r for r in results if not r.passed]
        if failed:
            raise AzureConfigurationError(results)

        logger.info("All Azure validation checks passed")
        return results
