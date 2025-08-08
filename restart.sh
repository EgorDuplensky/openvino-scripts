#!/usr/bin/env bash
#
# restart.sh  ―  Interactive PR and check selection with fzf
#

# set -euo pipefail

# Jenkins configuration
authfile="$HOME/.authinfo"

JENKINS_URL=$(awk '
  $1=="machine" { print $2; exit }  # first “machine …” line → field 2
' "$authfile")

TLS="-k"

# Check dependencies
command -v fzf >/dev/null 2>&1 || { echo "Error: fzf is required but not installed" >&2; exit 1; }
command -v gh >/dev/null 2>&1 || { echo "Error: gh CLI is required but not installed" >&2; exit 1; }
command -v curl >/dev/null 2>&1 || { echo "Error: curl is required but not installed" >&2; exit 1; }
command -v jq >/dev/null 2>&1 || { echo "Error: jq is required but not installed" >&2; exit 1; }

# Get current repo info
REPO=$(gh repo view --json nameWithOwner -q .nameWithOwner)
echo "Repository: $REPO"

# Function to format PR list for fzf with efficient status fetching
format_pr_list() {
    echo "Fetching your PRs with check status..."
    
    # Fetch all PR data with check status in one API call
    local pr_data
    pr_data=$(gh pr list --author "@me" --repo "$REPO" --limit 50 \
        --json number,title,author,headRefOid,statusCheckRollup)
    
    # Process the data to get CI state for each PR
    echo "$pr_data" | jq -r '
        map({
            number,
            title,
            author: .author.login,
            headRefOid,
            ci_state: (
                [ .statusCheckRollup[]
                    | if .__typename == "CheckRun" then .conclusion
                      else .state end ] as $states
                
                | if any($states[]; test("FAIL|ERROR|CANCEL|TIMED_OUT|failure|error|cancelled|timed_out"))
                  then "FAILURE"
                  elif any($states[]; test("PENDING|QUEUED|IN_PROGRESS|REQUESTED|pending|queued|in_progress|requested"))
                  then "PENDING"
                  else "SUCCESS"
                  end
            ),
            failed_count: (
                [ .statusCheckRollup[]
                    | if .__typename == "CheckRun" then .conclusion
                      else .state end ] 
                | map(select(test("FAIL|ERROR|CANCEL|TIMED_OUT|failure|error|cancelled|timed_out"))) 
                | length
            ),
            pending_count: (
                [ .statusCheckRollup[]
                    | if .__typename == "CheckRun" then .conclusion
                      else .state end ] 
                | map(select(test("PENDING|QUEUED|IN_PROGRESS|REQUESTED|pending|queued|in_progress|requested"))) 
                | length
            )
        }) | .[] | "\(.number)|\(.title)|@\(.author)|\(.headRefOid)|\(.ci_state)|\(.failed_count)|\(.pending_count)"
    ' | while IFS='|' read -r number title author _ ci_state failed_count pending_count; do
        # Format status indicator
        case "$ci_state" in
            "FAILURE") status="❌($failed_count)" ;;
            "PENDING") status="🟡($pending_count)" ;;
            "SUCCESS") status="✅" ;;
            *) status="❓" ;;
        esac
        
        printf "%-4s %s %-20s %s\n" "#$number" "$status" "[$author]" "$title"
    done
}

# Step 1: Select PR
echo "Select a PR:"
selected_pr=$(format_pr_list | fzf --ansi --header="Select PR to restart checks (❌=failed, 🟡=pending, ✅=success)" \
    --preview='echo {} | cut -d" " -f1 | sed "s/#//" | xargs -I{} gh pr view {} --json title,body,headRefOid -q "\"Title: \" + .title + \"\n\nBody:\n\" + .body"' \
    --preview-window=right:60%:wrap)

if [[ -z "$selected_pr" ]]; then
    echo "No PR selected, exiting."
    exit 0
fi

PR_NUMBER=$(echo "$selected_pr" | cut -d' ' -f1 | sed 's/#//')
echo "Selected PR: $PR_NUMBER"

# Get SHA for the selected PR
SHA=$(gh pr view "$PR_NUMBER" --json headRefOid -q .headRefOid)
if [[ -z "$SHA" ]]; then
    echo "Error: Could not get SHA for PR $PR_NUMBER"
    exit 1
fi

# Step 2: Get failed/pending workflow runs and Jenkins jobs
echo "Fetching GitHub Actions workflow runs for PR $PR_NUMBER..."
workflow_data=$(gh run list --repo "$REPO" --commit "$SHA" \
    --json databaseId,name,conclusion,status,workflowName,createdAt \
    --jq '.[] | select(.conclusion=="failure" or .status=="in_progress" or .status=="queued" or .status=="pending") | 
          "github|\(.databaseId)|\(.workflowName)|\(.name)|\(.conclusion // .status)|\(.createdAt)"')

echo "Fetching Jenkins jobs for PR $PR_NUMBER..."
# Get failed Jenkins jobs from commit status
jenkins_data=$(gh api "repos/$REPO/commits/$SHA/status" 2>/dev/null | \
    jq -r '.statuses[] 
           | select(.state=="failure" or .state=="error") 
           | select(.target_url | contains("job/github_trigger/job") | not) 
           | "jenkins|\(.target_url)|\(.context)|Jenkins Job|\(.state)|\(.updated_at)"' || echo "")

# Combine GitHub Actions and Jenkins data
all_jobs_data=""
if [[ -n "$workflow_data" ]]; then
    all_jobs_data="$workflow_data"
fi
if [[ -n "$jenkins_data" ]]; then
    if [[ -n "$all_jobs_data" ]]; then
        all_jobs_data="$all_jobs_data\n$jenkins_data"
    else
        all_jobs_data="$jenkins_data"
    fi
fi

if [[ -z "$all_jobs_data" ]]; then
    echo "No failed or pending jobs found for PR $PR_NUMBER"
    exit 0
fi

# Format jobs for fzf selection (GitHub Actions + Jenkins)
format_jobs_list() {
    echo -e "$all_jobs_data" | while IFS='|' read -r type id workflow_name run_name conclusion created_at; do
        status_icon="❌"
        [[ "$conclusion" == "in_progress" || "$conclusion" == "pending" || "$conclusion" == "queued" ]] && status_icon="🟡"
        
        # Format type indicator
        type_icon="🔧"  # GitHub Actions
        [[ "$type" == "jenkins" ]] && type_icon="⚙️"  # Jenkins
        
        created_date=$(date -d "$created_at" '+%m-%d %H:%M' 2>/dev/null || echo "$created_at")
        printf "%-8s %s %s %-25s %s [%s]\n" "$type" "$type_icon" "$status_icon" "$workflow_name" "$run_name" "$created_date"
    done
}

# Function to generate preview for job selection
generate_job_preview() {
    local line="$1"
    local type
    local id
    type=$(echo "$line" | cut -d" " -f1)
    id=$(echo "$line" | cut -d" " -f2)
    
    if [[ "$type" == "github" ]]; then
        gh run view "$id" --repo "$REPO" --json jobs -q '.jobs[] | "Job: " + .name + " (" + .conclusion + ")"'
    else
        echo "Jenkins Job: $id"
        echo "URL: $id"
        echo ""
        echo "This is a Jenkins job that failed during the CI process."
        echo "It will be restarted using the Jenkins rebuild API."
    fi
}

# Export function for fzf preview
export -f generate_job_preview
# export REPO

# Step 3: Select jobs to restart
echo "Select jobs to restart:"
selected_runs=$(format_jobs_list | fzf -m \
    --bind 'ctrl-a:select-all' \
    --marker '↻ '   --color='marker:yellow' \
    --header="Select jobs to restart (🔧=GitHub Actions, ⚙️=Jenkins, TAB: multi-select, Ctrl+A: select all)" \
    --preview='generate_job_preview "{}"' \
    --preview-window=right:50%:wrap)

if [[ -z "$selected_runs" ]]; then
    echo "No jobs selected, exiting."
    exit 0
fi

# Function to restart GitHub Actions job
restart_github_job() {
    local run_id="$1"
    local workflow_name="$2"
    
    if gh run rerun "$run_id" --repo "$REPO" --failed 2>/dev/null; then
        return 0
    else
        return 1
    fi
}

# Function to restart Jenkins job
restart_jenkins_job() {
    local build_url="$1"
    
    # Get CSRF crumb for Jenkins
    local crumb
    crumb=$(curl -s $TLS --netrc-file "$HOME/.authinfo" "$JENKINS_URL/crumbIssuer/api/json" 2>/dev/null | jq -r .crumb 2>/dev/null || echo "")
    
    if [[ -z "$crumb" ]]; then
        return 1
    fi
    
    # Rebuild the job
    local rebuild_url="${build_url%/}/rebuild?autorebuild=true"
    if curl -sS -L $TLS --netrc-file "$HOME/.authinfo" -H "Jenkins-Crumb:$crumb" -X POST "${rebuild_url}" >/dev/null 2>&1; then
        return 0
    else
        return 1
    fi
}

# Wrapper function to restart jobs based on type
restart_job() {
    local job_type="$1"
    local job_id="$2"
    local workflow_name="$3"
    
    case "$job_type" in
        "github")
            restart_github_job "$job_id" "$workflow_name"
            ;;
        "jenkins")
            restart_jenkins_job "$job_id" "$workflow_name"
            ;;
        *)
            return 1
            ;;
    esac
}

# Step 4: Restart selected jobs
echo "Restarting selected jobs..."
success_count=0
fail_count=0

while read -r line; do
    job_type=$(echo "$line" | cut -d' ' -f1)
    job_id=$(echo "$line" | cut -d' ' -f3)
    workflow_name=$(echo "$line" | cut -d' ' -f5)
    
    echo "↻ Restarting $job_type job: $workflow_name"
    
    if restart_job "$job_type" "$job_id" "$workflow_name"; then
        echo "✅ Successfully restarted $job_type job: $workflow_name"
        ((success_count++))
    else
        echo "❌ Failed to restart $job_type job: $workflow_name"
        ((fail_count++))
    fi
    echo ""
done <<< "$selected_runs"

echo "Summary:"
echo "✅ Successfully restarted: $success_count jobs"
echo "❌ Failed to restart: $fail_count jobs"
echo ""
echo "Check the Actions tab and Jenkins to monitor the restarted jobs."
