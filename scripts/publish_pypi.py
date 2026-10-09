"""Secure PyPI / TestPyPI publishing script using local .env credentials.

Reads TWINE_USERNAME and TWINE_PASSWORD from local .env or environment variables,
validates built artifacts in dist/, and invokes Twine upload securely through
subprocess environment variables without exposing credentials in command-line arguments,
logs, or persistent files.
"""

import argparse
import os
from pathlib import Path
import subprocess
import sys
from typing import List, Optional, Tuple

PROJECT_ROOT = Path(__file__).resolve().parent.parent


def get_default_env_path() -> Path:
    """Return the default local .env file path."""
    return PROJECT_ROOT / ".env"


def load_publishing_credentials(env_file: Optional[Path] = None) -> Tuple[str, str, Optional[str]]:
    """Load and validate Twine credentials from local .env or system environment.

    Returns:
        Tuple of (username, password, repository_url)

    Raises:
        ValueError: If TWINE_USERNAME or TWINE_PASSWORD is missing.
    """
    target_env = env_file or get_default_env_path()

    try:
        import dotenv
        if target_env.is_file():
            dotenv.load_dotenv(dotenv_path=target_env, override=True)
    except ImportError:
        # If python-dotenv is not installed, fallback to checking os.environ directly
        pass

    username = os.environ.get("TWINE_USERNAME", "").strip()
    password = os.environ.get("TWINE_PASSWORD", "").strip()
    repo_url = os.environ.get("TWINE_REPOSITORY_URL", "").strip() or None

    # Default username to __token__ if omitted but password token is present
    if not username and password:
        username = "__token__"

    if not username:
        raise ValueError(
            "Missing TWINE_USERNAME. Set TWINE_USERNAME in your local .env or environment."
        )

    if not password:
        raise ValueError(
            "Missing TWINE_PASSWORD. Set TWINE_PASSWORD (your PyPI token) in your local .env or environment."
        )

    return username, password, repo_url


def verify_dist_artifacts(dist_dir: Optional[Path] = None) -> List[Path]:
    """Verify built package artifacts exist in dist/ directory.

    Returns:
        List of Path objects for upload artifacts (.whl, .tar.gz).

    Raises:
        FileNotFoundError: If dist/ or required artifacts are missing.
    """
    target_dist = dist_dir or (PROJECT_ROOT / "dist")

    if not target_dist.is_dir():
        raise FileNotFoundError(
            f"Build artifact directory not found: {target_dist}. Run 'py -m build' first."
        )

    wheel_files = sorted(list(target_dist.glob("*.whl")))
    sdist_files = sorted(list(target_dist.glob("*.tar.gz")))

    if not wheel_files or not sdist_files:
        raise FileNotFoundError(
            f"Missing package artifacts in {target_dist}. Found {len(wheel_files)} wheels and "
            f"{len(sdist_files)} source archives. Run 'py -m build' first."
        )

    return wheel_files + sdist_files


def run_publish(
    username: str,
    password: str,
    dist_files: List[Path],
    repository_url: Optional[str] = None,
    extra_args: Optional[List[str]] = None,
    dry_run: bool = False,
) -> int:
    """Execute Twine upload with credentials securely injected into the subprocess environment."""
    if dry_run:
        print("\n" + "=" * 50)
        print("PyPI Publishing Pre-flight Check (Dry Run)")
        print("=" * 50)
        print(f"Username         : {username}")
        print("Password/Token   : [CONFIGURED - SECURELY MASKED]")
        if repository_url:
            print(f"Repository URL   : {repository_url}")
        print(f"Artifacts ({len(dist_files)}):")
        for f in dist_files:
            size_info = f" ({f.stat().st_size:,} bytes)" if f.exists() else ""
            print(f"  - {f.name}{size_info}")
        print("\n[OK] Validation successful. Ready for upload.")
        print("=" * 50 + "\n")
        return 0

    # Build subprocess environment with credentials
    upload_env = os.environ.copy()
    upload_env["TWINE_USERNAME"] = username
    upload_env["TWINE_PASSWORD"] = password
    if repository_url:
        upload_env["TWINE_REPOSITORY_URL"] = repository_url

    cmd = [
        sys.executable,
        "-m",
        "twine",
        "upload",
        "--non-interactive",
    ]
    if extra_args:
        cmd.extend(extra_args)

    for f in dist_files:
        cmd.append(str(f))

    print(f"\nUploading {len(dist_files)} artifact(s) via Twine...")
    result = subprocess.run(cmd, env=upload_env)
    return result.returncode


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(
        description="Securely publish package artifacts from dist/ to PyPI / TestPyPI using .env credentials."
    )
    parser.add_argument(
        "--env-file",
        type=str,
        default=None,
        help="Path to custom .env configuration file",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Validate credentials and artifacts without uploading",
    )
    parser.add_argument(
        "--repository-url",
        type=str,
        default=None,
        help="Custom repository upload URL (e.g. https://test.pypi.org/legacy/)",
    )
    parser.add_argument(
        "--repository",
        type=str,
        default=None,
        help="Twine repository name from .pypirc (e.g. testpypi or pypi)",
    )

    args, unknown_args = parser.parse_known_args(argv)

    env_path = Path(args.env_file).resolve() if args.env_file else None

    try:
        username, password, repo_url = load_publishing_credentials(env_file=env_path)
    except Exception as ex:
        print(f"Publishing Error: {ex}", file=sys.stderr)
        return 1

    try:
        artifacts = verify_dist_artifacts()
    except Exception as ex:
        print(f"Artifact Error: {ex}", file=sys.stderr)
        return 1

    target_repo_url = args.repository_url or repo_url
    extra_args = list(unknown_args)
    if args.repository:
        extra_args.extend(["--repository", args.repository])

    return run_publish(
        username=username,
        password=password,
        dist_files=artifacts,
        repository_url=target_repo_url,
        extra_args=extra_args,
        dry_run=args.dry_run,
    )


if __name__ == "__main__":
    sys.exit(main())
