#!/usr/bin/env python3
"""
restart.py - Interactive PR and check selection with fzf

A Python clone of restart.sh with improved modularity and readability.
Provides interactive selection of PRs and failed/pending CI jobs for restart.
"""

import json
import shutil
import subprocess
import sys
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional


@dataclass
class PRInfo:
    """Represents a Pull Request with its metadata."""
    number: int
    title: str
    author: str
    head_ref_oid: str
    ci_state: str
    failed_count: int
    pending_count: int


@dataclass
class JobInfo:
    """Represents a CI job (GitHub Actions or Jenkins)."""
    job_type: str  # 'github' or 'jenkins'
    job_id: str
    workflow_name: str
    run_name: str
    conclusion: str
    created_at: str


class DependencyChecker:
    """Checks for required command-line tools."""

    REQUIRED_TOOLS = ['fzf', 'gh', 'curl', 'jq']

    @staticmethod
    def check_dependencies() -> None:
        """Check if all required tools are available."""
        missing = []
        for tool in DependencyChecker.REQUIRED_TOOLS:
            if not shutil.which(tool):
                missing.append(tool)

        if missing:
            print(f"Error: Missing required tools: {', '.join(missing)}", file=sys.stderr)
            sys.exit(1)


class GitHubClient:
    """Handles GitHub API interactions using gh CLI."""

    def __init__(self):
        self.repo = self._get_current_repo()
        print(f"Repository: {self.repo}")

    def _get_current_repo(self) -> str:
        """Get current repository name using gh CLI."""
        try:
            result = subprocess.run(
                ['gh', 'repo', 'view', '--json', 'nameWithOwner', '-q', '.nameWithOwner'],
                capture_output=True, text=True, check=True
            )
            return result.stdout.strip()
        except subprocess.CalledProcessError as e:
            print(f"Error getting repository info: {e}", file=sys.stderr)
            sys.exit(1)

    def get_user_prs(self, limit: int = 50) -> List[PRInfo]:
        """Fetch user's PRs with CI status information."""
        print("Fetching your PRs with check status...")

        try:
            result = subprocess.run([
                'gh', 'pr', 'list', '--author', '@me', '--repo', self.repo,
                '--limit', str(limit), '--json',
                'number,title,author,headRefOid,statusCheckRollup'
            ], capture_output=True, text=True, check=True)

            pr_data = json.loads(result.stdout)
            return self._parse_pr_data(pr_data)
        except (subprocess.CalledProcessError, json.JSONDecodeError) as e:
            print(f"Error fetching PRs: {e}", file=sys.stderr)
            sys.exit(1)

    def _parse_pr_data(self, pr_data: List[Dict[str, Any]]) -> List[PRInfo]:
        """Parse PR data and calculate CI states."""
        prs = []

        for pr in pr_data:
            # Extract check states
            states = []
            for check in pr.get('statusCheckRollup', []):
                if check.get('__typename') == 'CheckRun':
                    states.append(check.get('conclusion', ''))
                else:
                    states.append(check.get('state', ''))

            # Determine overall CI state
            failed_states = {'FAILURE', 'ERROR', 'CANCELLED', 'TIMED_OUT',
                             'failure', 'error', 'cancelled', 'timed_out'}
            pending_states = {'PENDING', 'QUEUED', 'IN_PROGRESS', 'REQUESTED',
                              'pending', 'queued', 'in_progress', 'requested'}

            failed_count = sum(1 for state in states if state in failed_states)
            pending_count = sum(1 for state in states if state in pending_states)

            if any(state in failed_states for state in states):
                ci_state = 'FAILURE'
            elif any(state in pending_states for state in states):
                ci_state = 'PENDING'
            else:
                ci_state = 'SUCCESS'

            prs.append(PRInfo(
                number=pr['number'],
                title=pr['title'],
                author=pr['author']['login'],
                head_ref_oid=pr['headRefOid'],
                ci_state=ci_state,
                failed_count=failed_count,
                pending_count=pending_count
            ))

        return prs

    def get_pr_sha(self, pr_number: int) -> Optional[str]:
        """Get SHA for a specific PR."""
        try:
            result = subprocess.run([
                'gh', 'pr', 'view', str(pr_number),
                '--json', 'headRefOid', '-q', '.headRefOid'
            ], capture_output=True, text=True, check=True)
            return result.stdout.strip()
        except subprocess.CalledProcessError:
            return None

    def get_workflow_runs(self, sha: str) -> List[JobInfo]:
        """Get failed/pending GitHub Actions workflow runs."""
        try:
            result = subprocess.run([
                'gh', 'run', 'list', '--repo', self.repo, '--commit', sha,
                '--json', 'databaseId,name,conclusion,status,workflowName,createdAt'
            ], capture_output=True, text=True, check=True)

            runs_data = json.loads(result.stdout)
            jobs = []

            for run in runs_data:
                if (run.get('conclusion') == 'failure' or
                        run.get('status') in ['in_progress', 'queued', 'pending']):
                    jobs.append(JobInfo(
                        job_type='github',
                        job_id=str(run['databaseId']),
                        workflow_name=run['workflowName'],
                        run_name=run['name'],
                        conclusion=run.get('conclusion') or run.get('status'),
                        created_at=run['createdAt']
                    ))

            return jobs
        except (subprocess.CalledProcessError, json.JSONDecodeError):
            return []

    def get_jenkins_jobs(self, sha: str) -> List[JobInfo]:
        """Get failed Jenkins jobs from commit status."""
        try:
            result = subprocess.run([
                'gh', 'api', f'repos/{self.repo}/commits/{sha}/status'
            ], capture_output=True, text=True, check=True)

            status_data = json.loads(result.stdout)
            jobs = []

            for status in status_data.get('statuses', []):
                if (status.get('state') in ['failure', 'error'] and
                        'job/github_trigger/job' not in status.get('target_url', '')):
                    jobs.append(JobInfo(
                        job_type='jenkins',
                        job_id=status['target_url'],
                        workflow_name=status['context'],
                        run_name='Jenkins Job',
                        conclusion=status['state'],
                        created_at=status['updated_at']
                    ))

            return jobs
        except (subprocess.CalledProcessError, json.JSONDecodeError):
            return []

    def restart_github_job(self, run_id: str) -> bool:
        """Restart a GitHub Actions workflow run."""
        try:
            subprocess.run([
                'gh', 'run', 'rerun', run_id, '--repo', self.repo, '--failed'
            ], capture_output=True, check=True)
            return True
        except subprocess.CalledProcessError:
            return False


class JenkinsClient:
    """Handles Jenkins API interactions."""

    def __init__(self):
        self.jenkins_url = self._get_jenkins_url()
        self.auth_file = Path.home() / '.authinfo'
        self.tls_option = '-k'  # @todo: make configurable

    def _get_jenkins_url(self) -> Optional[str]:
        """Extract Jenkins URL from auth file."""
        auth_file = Path.home() / '.authinfo'
        try:
            with open(auth_file, 'r') as f:
                for line in f:
                    parts = line.strip().split()
                    if len(parts) >= 2 and parts[0] == 'machine':
                        return parts[1]
        except FileNotFoundError:
            pass
        return None

    def restart_jenkins_job(self, build_url: str) -> bool:
        """Restart a Jenkins job using rebuild API."""
        if not self.jenkins_url:
            return False

        try:
            # Get CSRF crumb
            crumb_url = f"{self.jenkins_url}/crumbIssuer/api/json"
            crumb_result = subprocess.run([
                'curl', '-s', self.tls_option, '--netrc-file', str(self.auth_file),
                crumb_url
            ], capture_output=True, text=True)

            if crumb_result.returncode != 0:
                return False

            try:
                crumb_data = json.loads(crumb_result.stdout)
                crumb = crumb_data.get('crumb')
            except json.JSONDecodeError:
                return False

            if not crumb:
                return False

            # Rebuild the job
            rebuild_url = f"{build_url.rstrip('/')}/rebuild?autorebuild=true"
            rebuild_result = subprocess.run([
                'curl', '-sS', '-L', self.tls_option, '--netrc-file', str(self.auth_file),
                '-H', f'Jenkins-Crumb:{crumb}', '-X', 'POST', rebuild_url
            ], capture_output=True)

            return rebuild_result.returncode == 0
        except Exception:
            return False


class FzfInterface:
    """Handles fzf interactions for user selection."""

    @staticmethod
    def select_pr(prs: List[PRInfo]) -> Optional[PRInfo]:
        """Let user select a PR using fzf."""
        if not prs:
            print("No PRs found.")
            return None

        # Format PR list for fzf
        pr_lines = []
        for pr in prs:
            status_icon = {
                'FAILURE': f'❌({pr.failed_count})',
                'PENDING': f'🟡({pr.pending_count})',
                'SUCCESS': '✅'
            }.get(pr.ci_state, '❓')

            line = f"#{pr.number} {status_icon} [@{pr.author}] {pr.title}"
            pr_lines.append(line)

        # Run fzf
        try:
            fzf_process = subprocess.Popen([
                'fzf', '--ansi',
                '--header=Select PR to restart checks (❌=failed, 🟡=pending, ✅=success)',
                '--preview=echo {} | cut -d" " -f1 | sed "s/#//" | xargs -I{} gh pr view {} --json title,body,headRefOid -q "\\"Title: \\" + .title + \\"\\n\\nBody:\\n\\" + .body"',  # noqa: E501
                '--preview-window=right:60%:wrap'
            ], stdin=subprocess.PIPE, stdout=subprocess.PIPE, text=True)

            selected, _ = fzf_process.communicate('\n'.join(pr_lines))

            if fzf_process.returncode != 0 or not selected.strip():
                return None

            # Parse selected PR number
            pr_number = int(selected.split()[0][1:])  # Remove # prefix
            return next((pr for pr in prs if pr.number == pr_number), None)

        except (subprocess.CalledProcessError, ValueError):
            return None

    @staticmethod
    def select_jobs(jobs: List[JobInfo]) -> List[JobInfo]:
        """Let user select jobs to restart using fzf."""
        if not jobs:
            print("No failed or pending jobs found.")
            return []

        # Format job list for fzf
        job_lines = []
        for i, job in enumerate(jobs):
            status_icon = '🟡' if job.conclusion in ['in_progress', 'pending', 'queued'] else '❌'
            type_icon = '🔧' if job.job_type == 'github' else '⚙️'

            try:
                created_date = datetime.fromisoformat(job.created_at.replace('Z', '+00:00'))
                date_str = created_date.strftime('%m-%d %H:%M')
            except (ValueError, AttributeError):
                date_str = job.created_at

            line = f"{job.job_type} {type_icon} {status_icon} {job.workflow_name:<25} {job.run_name} [{date_str}]"
            job_lines.append((line, i))

        # Run fzf with multi-select
        try:
            fzf_process = subprocess.Popen([
                'fzf', '-m', '--bind', 'ctrl-a:select-all',
                '--marker', '↻ ', '--color', 'marker:yellow',
                '--header=Select jobs to restart (🔧=GitHub Actions, ⚙️=Jenkins, TAB: multi-select, Ctrl+A: select all)',
                '--preview=echo "Job details preview"',  # @todo: implement proper preview
                '--preview-window=right:50%:wrap'
            ], stdin=subprocess.PIPE, stdout=subprocess.PIPE, text=True)

            selected, _ = fzf_process.communicate('\n'.join(line for line, _ in job_lines))

            if fzf_process.returncode != 0 or not selected.strip():
                return []

            # Match selected lines to jobs
            selected_lines = selected.strip().split('\n')
            selected_jobs = []

            for selected_line in selected_lines:
                for line, index in job_lines:
                    if line == selected_line:
                        selected_jobs.append(jobs[index])
                        break

            return selected_jobs

        except subprocess.CalledProcessError:
            return []


class RestartManager:
    """Main class orchestrating the PR and job restart process."""

    def __init__(self):
        self.github = GitHubClient()
        self.jenkins = JenkinsClient()
        self.fzf = FzfInterface()

    def run(self) -> None:
        """Main execution flow."""
        # Step 1: Select PR
        print("Select a PR:")
        prs = self.github.get_user_prs()
        selected_pr = self.fzf.select_pr(prs)

        if not selected_pr:
            print("No PR selected, exiting.")
            return

        print(f"Selected PR: {selected_pr.number}")

        # Get SHA for selected PR
        sha = self.github.get_pr_sha(selected_pr.number)
        if not sha:
            print(f"Error: Could not get SHA for PR {selected_pr.number}")
            return

        # Step 2: Get failed/pending jobs
        print(f"Fetching GitHub Actions workflow runs for PR {selected_pr.number}...")
        github_jobs = self.github.get_workflow_runs(sha)

        print(f"Fetching Jenkins jobs for PR {selected_pr.number}...")
        jenkins_jobs = self.github.get_jenkins_jobs(sha)

        all_jobs = github_jobs + jenkins_jobs

        if not all_jobs:
            print(f"No failed or pending jobs found for PR {selected_pr.number}")
            return

        # Step 3: Select jobs to restart
        print("Select jobs to restart:")
        selected_jobs = self.fzf.select_jobs(all_jobs)

        if not selected_jobs:
            print("No jobs selected, exiting.")
            return

        # Step 4: Restart selected jobs
        print("Restarting selected jobs...")
        success_count = 0
        fail_count = 0

        for job in selected_jobs:
            print(f"↻ Restarting {job.job_type} job: {job.workflow_name}")

            success = False
            if job.job_type == 'github':
                success = self.github.restart_github_job(job.job_id)
            elif job.job_type == 'jenkins':
                success = self.jenkins.restart_jenkins_job(job.job_id)

            if success:
                print(f"✅ Successfully restarted {job.job_type} job: {job.workflow_name}")
                success_count += 1
            else:
                print(f"❌ Failed to restart {job.job_type} job: {job.workflow_name}")
                fail_count += 1
            print()

        # Summary
        print("Summary:")
        print(f"✅ Successfully restarted: {success_count} jobs")
        print(f"❌ Failed to restart: {fail_count} jobs")
        print()
        print("Check the Actions tab and Jenkins to monitor the restarted jobs.")


def main() -> None:
    """Entry point."""
    try:
        DependencyChecker.check_dependencies()
        manager = RestartManager()
        manager.run()
    except KeyboardInterrupt:
        print("\nOperation cancelled by user.")
        sys.exit(1)
    except Exception as e:
        print(f"Unexpected error: {e}", file=sys.stderr)
        sys.exit(1)


if __name__ == '__main__':
    main()
