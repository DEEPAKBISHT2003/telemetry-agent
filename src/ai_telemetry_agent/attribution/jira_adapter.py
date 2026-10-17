"""Optional read-only Jira API client adapter interface and implementations."""

from abc import ABC, abstractmethod
from dataclasses import dataclass
import json
import os
from typing import Any, Dict, Optional
import urllib.error
import urllib.request

from ai_telemetry_agent.logging.structured import get_logger

logger = get_logger("telemetry.attribution.jira")


@dataclass
class JiraIssueInfo:
    """Read-only summary of Jira issue resolved via API."""
    key: str
    title: Optional[str] = None
    status: Optional[str] = None
    url: Optional[str] = None


import urllib.parse


def normalize_jira_base_url(url: Optional[str]) -> Optional[str]:
    """Validate and normalize a Jira base URL using secure scheme and host validation."""
    if not url or not str(url).strip():
        return None
    trimmed = str(url).strip()
    if "://" in trimmed:
        scheme = trimmed.split("://", 1)[0].lower()
        if scheme not in ("http", "https"):
            return None
    else:
        trimmed = "https://" + trimmed
    parsed = urllib.parse.urlparse(trimmed)
    if parsed.scheme not in ("http", "https"):
        return None
    if not parsed.netloc:
        return None
    # Strip any path like /jira or /rest/api/... from base url if provided by accident,
    # or preserve base path if enterprise server uses a subpath (e.g. https://corp.com/jira)
    clean_path = parsed.path.rstrip("/")
    # If path ends with /rest/api/..., strip it back
    if "/rest/api" in clean_path:
        clean_path = clean_path.split("/rest/api")[0].rstrip("/")
    netloc = parsed.netloc.lower()
    return f"{parsed.scheme}://{netloc}{clean_path}".rstrip("/")


class JiraAdapter(ABC):
    """Abstract interface for read-only Jira operations.
    
    All methods must degrade gracefully when Jira is offline or unconfigured.
    """

    @abstractmethod
    def get_issue(self, issue_key: str) -> Optional[JiraIssueInfo]:
        """Fetch title and status for an issue key. Returns None if unreachable or unresolved."""
        pass

    @abstractmethod
    def test_connection(self) -> Dict[str, Any]:
        """Verify authentication and API connectivity via read-only /myself endpoint.
        
        Returns:
            Dict containing status ('success', 'auth_error', 'permission_denied', 'offline', 'network_error', 'error'),
            message, and safe account details (never credentials).
        """
        pass

    @abstractmethod
    def test_issue(self, issue_key: str) -> Dict[str, Any]:
        """Perform a read-only issue lookup check for an issue key.
        
        Returns:
            Dict containing status ('success', 'not_found', 'auth_error', 'permission_denied', 'network_error', 'timeout', 'offline', 'error'),
            key, message, and issue metadata if available.
        """
        pass

    @abstractmethod
    def is_configured(self) -> bool:
        """Return True if Jira credentials and base URL are configured."""
        pass


class NullJiraAdapter(JiraAdapter):
    """Default offline adapter used when Jira API is not configured or disabled."""

    def get_issue(self, issue_key: str) -> Optional[JiraIssueInfo]:
        return None

    def test_connection(self) -> Dict[str, Any]:
        return {
            "status": "offline",
            "message": "Jira integration is unconfigured or disabled (JIRA_BASE_URL is not set).",
        }

    def test_issue(self, issue_key: str) -> Dict[str, Any]:
        clean_key = (issue_key or "").strip().upper()
        return {
            "status": "offline",
            "key": clean_key,
            "message": "Jira integration is unconfigured or disabled (JIRA_BASE_URL is not set).",
        }

    def is_configured(self) -> bool:
        return False


class GenericJiraRestAdapter(JiraAdapter):
    """Read-only Jira REST API adapter supporting Cloud, Server, and Data Center deployments.
    
    - Cloud deployments (e.g. *.atlassian.net) use HTTP Basic Auth with account email + API token.
    - Server/Data Center deployments use Personal Access Tokens (Bearer) or custom auth.
    - Does NOT mutate issues, projects, or permissions.
    - Reads credentials strictly from memory/environment, never persisting tokens to telemetry or logs.
    """

    def __init__(
        self,
        base_url: str,
        auth_type: str = "pat",  # "pat", "bearer", "basic", "token"
        api_token: Optional[str] = None,
        username: Optional[str] = None,
        timeout_seconds: float = 5.0,
    ):
        self.base_url = normalize_jira_base_url(base_url) or ""
        parsed_host = urllib.parse.urlparse(self.base_url).netloc.lower() if self.base_url else ""
        raw_auth = (auth_type or "pat").lower().strip()

        # If connecting to Atlassian Cloud (*.atlassian.net) and a username (email) is present,
        # default to basic authentication automatically unless explicitly overridden.
        if "atlassian.net" in parsed_host and username and raw_auth in ("pat", "bearer", "token", "basic"):
            self.auth_type = "basic"
        else:
            self.auth_type = raw_auth

        self.api_token = api_token
        self.username = username
        self.timeout_seconds = timeout_seconds

    def is_configured(self) -> bool:
        return bool(self.base_url)

    def _build_headers(self) -> Dict[str, str]:
        headers = {
            "Accept": "application/json",
            "User-Agent": "ai-telemetry-agent/0.1.2",
        }
        if not self.api_token:
            return headers

        if self.auth_type in ("pat", "bearer", "token"):
            headers["Authorization"] = f"Bearer {self.api_token}"
        elif self.auth_type == "basic" and self.username:
            import base64
            token_str = f"{self.username}:{self.api_token}"
            encoded = base64.b64encode(token_str.encode("utf-8")).decode("utf-8")
            headers["Authorization"] = f"Basic {encoded}"
        return headers

    def test_connection(self) -> Dict[str, Any]:
        """Perform a read-only authentication check via /rest/api/2/myself."""
        if not self.base_url:
            return {
                "status": "offline",
                "message": "Jira base URL is not configured.",
            }
        if not self.api_token:
            return {
                "status": "auth_error",
                "message": "JIRA_API_TOKEN is not set.",
            }
        if self.auth_type == "basic" and not self.username:
            return {
                "status": "auth_error",
                "message": "JIRA_USERNAME (account email) is required for basic/Cloud authentication.",
            }

        url = f"{self.base_url}/rest/api/2/myself"
        req = urllib.request.Request(url, headers=self._build_headers(), method="GET")

        try:
            with urllib.request.urlopen(req, timeout=self.timeout_seconds) as resp:
                status_code = resp.status
                if status_code == 200:
                    data = json.loads(resp.read().decode("utf-8"))
                    display_name = data.get("displayName")
                    email = data.get("emailAddress")
                    active = data.get("active", True)
                    time_zone = data.get("timeZone")
                    account_type = data.get("accountType")
                    return {
                        "status": "success",
                        "status_code": 200,
                        "base_url": self.base_url,
                        "auth_type": self.auth_type,
                        "display_name": display_name,
                        "email": email,
                        "active": active,
                        "time_zone": time_zone,
                        "account_type": account_type,
                        "message": "Successfully authenticated with Jira REST API.",
                    }
                else:
                    return {
                        "status": "unexpected_response",
                        "status_code": status_code,
                        "message": f"Unexpected server response (HTTP {status_code}).",
                    }
        except urllib.error.HTTPError as he:
            if he.code == 401:
                return {
                    "status": "auth_error",
                    "status_code": 401,
                    "message": "Authentication failed (HTTP 401 Unauthorized). Verify your API token and username.",
                }
            elif he.code == 403:
                return {
                    "status": "permission_denied",
                    "status_code": 403,
                    "message": "Access forbidden (HTTP 403 Forbidden). Check account permissions.",
                }
            elif he.code == 404:
                return {
                    "status": "not_found",
                    "status_code": 404,
                    "message": "API endpoint not found (HTTP 404). Verify JIRA_BASE_URL.",
                }
            else:
                return {
                    "status": "error",
                    "status_code": he.code,
                    "message": f"Jira API returned HTTP {he.code}.",
                }
        except urllib.error.URLError as ue:
            return {
                "status": "network_error",
                "message": f"Network connection failed: {ue.reason}",
            }
        except TimeoutError:
            return {
                "status": "timeout",
                "message": f"Connection timed out after {self.timeout_seconds}s.",
            }
        except Exception as ex:
            return {
                "status": "error",
                "message": f"Unexpected connection error: {type(ex).__name__}",
            }

    def test_issue(self, issue_key: str) -> Dict[str, Any]:
        """Perform a read-only issue lookup check for an issue key."""
        if not self.base_url or not issue_key:
            return {
                "status": "error",
                "message": "Base URL or issue key missing.",
            }
        clean_key = issue_key.strip().upper()
        url = f"{self.base_url}/rest/api/2/issue/{clean_key}?fields=summary,status"
        req = urllib.request.Request(url, headers=self._build_headers(), method="GET")

        try:
            with urllib.request.urlopen(req, timeout=self.timeout_seconds) as resp:
                if resp.status == 200:
                    data = json.loads(resp.read().decode("utf-8"))
                    fields = data.get("fields", {})
                    title = fields.get("summary")
                    status_obj = fields.get("status")
                    status = status_obj.get("name") if isinstance(status_obj, dict) else None
                    return {
                        "status": "success",
                        "status_code": 200,
                        "key": clean_key,
                        "title": title,
                        "issue_status": status,
                        "url": f"{self.base_url}/browse/{clean_key}",
                        "message": f"Successfully retrieved issue '{clean_key}'.",
                    }
                else:
                    return {
                        "status": "unexpected_response",
                        "status_code": resp.status,
                        "key": clean_key,
                        "message": f"Unexpected response HTTP {resp.status}.",
                    }
        except urllib.error.HTTPError as he:
            if he.code == 404:
                return {
                    "status": "not_found",
                    "status_code": 404,
                    "key": clean_key,
                    "message": f"Issue '{clean_key}' not found or inaccessible (HTTP 404).",
                }
            elif he.code in (401, 403):
                return {
                    "status": "permission_denied" if he.code == 403 else "auth_error",
                    "status_code": he.code,
                    "key": clean_key,
                    "message": f"Access denied for issue '{clean_key}' (HTTP {he.code}).",
                }
            else:
                return {
                    "status": "error",
                    "status_code": he.code,
                    "key": clean_key,
                    "message": f"HTTP {he.code} error looking up issue '{clean_key}'.",
                }
        except urllib.error.URLError as ue:
            return {
                "status": "network_error",
                "key": clean_key,
                "message": f"Network connection failed: {ue.reason}",
            }
        except TimeoutError:
            return {
                "status": "timeout",
                "key": clean_key,
                "message": f"Connection timed out after {self.timeout_seconds}s.",
            }
        except Exception as ex:
            return {
                "status": "error",
                "key": clean_key,
                "message": f"Failed looking up issue '{clean_key}': {type(ex).__name__}",
            }

    def get_issue(self, issue_key: str) -> Optional[JiraIssueInfo]:
        if not self.base_url or not issue_key:
            return None

        clean_key = issue_key.strip().upper()
        # Default Server/Data Center and Cloud issue endpoint
        url = f"{self.base_url}/rest/api/2/issue/{clean_key}?fields=summary,status"

        req = urllib.request.Request(url, headers=self._build_headers(), method="GET")
        try:
            with urllib.request.urlopen(req, timeout=self.timeout_seconds) as resp:
                if resp.status != 200:
                    return None
                data = json.loads(resp.read().decode("utf-8"))
                fields = data.get("fields", {})
                title = fields.get("summary")
                status_obj = fields.get("status")
                status = status_obj.get("name") if isinstance(status_obj, dict) else None
                issue_url = f"{self.base_url}/browse/{clean_key}"
                return JiraIssueInfo(key=clean_key, title=title, status=status, url=issue_url)
        except (urllib.error.HTTPError, urllib.error.URLError, TimeoutError, OSError) as e:
            logger.debug("jira_api_fetch_failed", issue_key=clean_key, error=str(e))
            return None
        except Exception as e:
            logger.debug("jira_api_unexpected_error", issue_key=clean_key, error=str(e))
            return None


def get_jira_adapter_from_env() -> JiraAdapter:
    """Instantiate appropriate Jira adapter from environment variables."""
    base_url = os.environ.get("JIRA_BASE_URL")
    if not base_url or not base_url.strip():
        return NullJiraAdapter()

    auth_type = os.environ.get("JIRA_AUTH_TYPE", "pat")
    api_token = os.environ.get("JIRA_API_TOKEN") or os.environ.get("JIRA_PAT")
    username = os.environ.get("JIRA_USERNAME")

    return GenericJiraRestAdapter(
        base_url=base_url.strip(),
        auth_type=auth_type,
        api_token=api_token,
        username=username,
    )
