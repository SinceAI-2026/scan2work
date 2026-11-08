#!/usr/bin/env bash
# Fresh, dedicated-project infrastructure. No cloud calls without --apply.
set -euo pipefail
SCRIPT_DIR=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)
APPLY=0; CREATE_PROJECT=0; LINK_BILLING=0
PROJECT_ID=; BILLING_ACCOUNT=; OPERATOR=; BUDGET_AMOUNT=; BUDGET_CURRENCY=EUR
REGION=europe-north1; ZONE=europe-north1-b; BACKUP_BUCKET=
PARENT_KIND=; PARENT_ID=
usage() {
  cat <<'HELP'
Usage: provision.sh --project ID --operator user:EMAIL [options]
  --apply                       Create resources; otherwise print the plan only
  --create-project              Create a dedicated project (no default project)
  --link-billing                Explicitly attach the supplied billing account
  --billing-account ID          Required for billing linking or a budget alert
  --folder ID | --organization ID  Optional parent for a newly created project
  --zone ZONE                   Default europe-north1-b; must be in europe-north1
  --backup-bucket NAME          Default PROJECT-scan2work-state
  --budget-amount AMOUNT         Optional project-filtered monthly alert, NOT a cap
  --budget-currency CODE        Billing account currency; default EUR
This is a fresh-install script, not an updater. It refuses an existing VM,
data disk, VPC, runtime service account or backup bucket instead of adopting it.
HELP
}
die() { printf 'Error: %s\n' "$*" >&2; exit 1; }
while (($#)); do
  case "$1" in
    --apply) APPLY=1; shift ;;
    --create-project) CREATE_PROJECT=1; shift ;;
    --link-billing) LINK_BILLING=1; shift ;;
    --project|--operator|--billing-account|--budget-amount|--budget-currency|--zone|--backup-bucket|--folder|--organization)
      (($# >= 2)) || die "Missing value for $1"
      case "$1" in
        --project) PROJECT_ID=$2 ;; --operator) OPERATOR=$2 ;;
        --billing-account) BILLING_ACCOUNT=$2 ;; --budget-amount) BUDGET_AMOUNT=$2 ;; --budget-currency) BUDGET_CURRENCY=$2 ;;
        --zone) ZONE=$2 ;; --backup-bucket) BACKUP_BUCKET=$2 ;;
        --folder|--organization)
          [[ -z "$PARENT_KIND" ]] || die 'Choose only one project parent'
          PARENT_KIND=$1; PARENT_ID=$2 ;;
      esac
      shift 2 ;;
    -h|--help) usage; exit 0 ;;
    *) die "Unknown argument: $1" ;;
  esac
done
[[ "$PROJECT_ID" =~ ^[a-z][a-z0-9-]{4,28}[a-z0-9]$ ]] || die 'Supply an explicit valid --project ID'
[[ "$OPERATOR" =~ ^user:[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+$ ]] || die 'Supply --operator user:EMAIL for IAP/OS Login'
[[ "$ZONE" =~ ^europe-north1-[abc]$ ]] || die 'This plan is limited to europe-north1'
[[ -z "$PARENT_KIND" || "$CREATE_PROJECT" == 1 ]] || die 'A parent only applies with --create-project'
[[ -z "$PARENT_ID" || "$PARENT_ID" =~ ^[0-9]+$ ]] || die 'Project parent must be numeric'
[[ "$CREATE_PROJECT" == 0 || "$LINK_BILLING" == 1 ]] || die 'New projects require explicit --link-billing'
if [[ "$LINK_BILLING" == 1 || -n "$BUDGET_AMOUNT" ]]; then
  [[ "$BILLING_ACCOUNT" =~ ^[A-Z0-9]{6}-[A-Z0-9]{6}-[A-Z0-9]{6}$ ]] || die 'Supply a valid --billing-account'
fi
[[ -z "$BUDGET_AMOUNT" || "$BUDGET_AMOUNT" =~ ^[0-9]+([.][0-9]{1,2})?$ ]] || die 'Budget must be a positive amount'
[[ -z "$BUDGET_AMOUNT" || ! "$BUDGET_AMOUNT" =~ ^0+([.]0+)?$ ]] || die 'Budget must be positive'
[[ "$BUDGET_CURRENCY" =~ ^[A-Z]{3}$ ]] || die 'Budget currency must be a three-letter code matching the billing account'
BACKUP_BUCKET=${BACKUP_BUCKET:-${PROJECT_ID}-scan2work-state}
[[ "$BACKUP_BUCKET" =~ ^[a-z0-9][a-z0-9-]{1,61}[a-z0-9]$ ]] || die 'Use a valid 3–63 character backup bucket name'
[[ -f "$SCRIPT_DIR/startup.sh" && -f "$SCRIPT_DIR/backup-lifecycle.json" && -f "$SCRIPT_DIR/check-quotas.py" ]] || die 'Missing adjacent provisioning files'
RUNTIME_SA="scan2work-runtime@${PROJECT_ID}.iam.gserviceaccount.com"
GCLOUD=(gcloud --quiet --project="$PROJECT_ID")
run() {
  printf '+'; printf ' %q' "$@"; printf '\n'
  if [[ "$APPLY" == 1 ]]; then "$@"; fi
}
printf 'Scan2Work %s: %s / %s; e2-medium, 4 GiB, no GPU; boot 20 GiB + retained data 30 GiB\n' \
  "$([[ "$APPLY" == 1 ]] && printf APPLY || printf PLAN)" "$PROJECT_ID" "$ZONE"
printf 'Creates public 80/443 only; SSH via IAP; private backup bucket gs://%s\n' "$BACKUP_BUCKET"
if [[ "$APPLY" == 1 ]]; then
  command -v gcloud >/dev/null || die 'Install/authenticate gcloud first'
  command -v python3 >/dev/null || die 'Python 3 is needed to check regional quotas'
  bash -n "$SCRIPT_DIR/startup.sh"
  if [[ "$CREATE_PROJECT" == 1 ]]; then
    if gcloud projects describe "$PROJECT_ID" --format='value(projectId)' >/dev/null 2>&1; then
      die 'Project already exists; inspect it and omit --create-project if appropriate'
    fi
  else
    gcloud projects describe "$PROJECT_ID" --format='value(projectId)' >/dev/null
  fi
fi
if [[ "$CREATE_PROJECT" == 1 ]]; then
  PARENT_ARGS=(); [[ -z "$PARENT_KIND" ]] || PARENT_ARGS=("$PARENT_KIND=$PARENT_ID")
  run gcloud --quiet projects create "$PROJECT_ID" --name='Scan2Work Demo' "${PARENT_ARGS[@]}"
fi
if [[ "$LINK_BILLING" == 1 ]]; then
  run gcloud --quiet billing projects link "$PROJECT_ID" --billing-account="$BILLING_ACCOUNT"
fi
if [[ "$APPLY" == 1 ]]; then
  [[ "$(gcloud billing projects describe "$PROJECT_ID" --format='value(billingEnabled)')" == True ]] || die 'Project billing is not enabled'
fi
run "${GCLOUD[@]}" services enable compute.googleapis.com iam.googleapis.com storage.googleapis.com iap.googleapis.com oslogin.googleapis.com
run "${GCLOUD[@]}" compute machine-types describe e2-medium --zone="$ZONE" --format='table(name,guestCpus,memoryMb)'
printf '+ Check %s CPU, instance, disk and external IP quota before allocating resources\n' "$REGION"
if [[ "$APPLY" == 1 ]]; then
  "${GCLOUD[@]}" compute regions describe "$REGION" --format=json | python3 "$SCRIPT_DIR/check-quotas.py"
fi
if [[ "$APPLY" == 1 ]]; then
  # These are all read-only checks. A partial earlier install needs explicit review.
  for resource in instance disk network service-account bucket address; do
    exists=0
    case "$resource" in
      instance) "${GCLOUD[@]}" compute instances describe scan2work-cpu --zone="$ZONE" >/dev/null 2>&1 && exists=1 ;;
      disk) "${GCLOUD[@]}" compute disks describe scan2work-data --zone="$ZONE" >/dev/null 2>&1 && exists=1 ;;
      network) "${GCLOUD[@]}" compute networks describe scan2work-net >/dev/null 2>&1 && exists=1 ;;
      service-account) "${GCLOUD[@]}" iam service-accounts describe "$RUNTIME_SA" >/dev/null 2>&1 && exists=1 ;;
      bucket) "${GCLOUD[@]}" storage buckets describe "gs://$BACKUP_BUCKET" >/dev/null 2>&1 && exists=1 ;;
      address) "${GCLOUD[@]}" compute addresses describe scan2work-origin --region="$REGION" >/dev/null 2>&1 && exists=1 ;;
    esac
    [[ "$exists" == 0 ]] || die "Existing $resource found. Stop and review; do not delete the retained data disk to rerun this script."
  done
fi
run "${GCLOUD[@]}" iam service-accounts create scan2work-runtime --display-name='Scan2Work backup writer'
run "${GCLOUD[@]}" storage buckets create "gs://$BACKUP_BUCKET" --location="$REGION" \
  --default-storage-class=STANDARD --uniform-bucket-level-access --public-access-prevention \
  --retention-period=30d --soft-delete-duration=7d
run "${GCLOUD[@]}" storage buckets update "gs://$BACKUP_BUCKET" --lifecycle-file="$SCRIPT_DIR/backup-lifecycle.json"
run "${GCLOUD[@]}" storage buckets add-iam-policy-binding "gs://$BACKUP_BUCKET" \
  --member="serviceAccount:$RUNTIME_SA" --role=roles/storage.objectCreator
run "${GCLOUD[@]}" compute networks create scan2work-net --subnet-mode=custom --bgp-routing-mode=regional
run "${GCLOUD[@]}" compute networks subnets create scan2work-subnet --network=scan2work-net \
  --region="$REGION" --range=10.80.0.0/24
run "${GCLOUD[@]}" compute firewall-rules create scan2work-https --network=scan2work-net \
  --direction=INGRESS --allow=tcp:80,tcp:443 --source-ranges=0.0.0.0/0 --target-tags=scan2work-web
run "${GCLOUD[@]}" compute firewall-rules create scan2work-iap-ssh --network=scan2work-net \
  --direction=INGRESS --allow=tcp:22 --source-ranges=35.235.240.0/20 --target-tags=scan2work-ssh
run "${GCLOUD[@]}" projects add-iam-policy-binding "$PROJECT_ID" --member="$OPERATOR" --role=roles/compute.osAdminLogin
run "${GCLOUD[@]}" projects add-iam-policy-binding "$PROJECT_ID" --member="$OPERATOR" --role=roles/iap.tunnelResourceAccessor
run "${GCLOUD[@]}" iam service-accounts add-iam-policy-binding "$RUNTIME_SA" --member="$OPERATOR" --role=roles/iam.serviceAccountUser
run "${GCLOUD[@]}" compute addresses create scan2work-origin --region="$REGION" --network-tier=STANDARD
run "${GCLOUD[@]}" compute disks create scan2work-data --zone="$ZONE" --size=30GB --type=pd-standard \
  --labels=app=scan2work,role=persistent-data
run "${GCLOUD[@]}" compute instances create scan2work-cpu --zone="$ZONE" --machine-type=e2-medium \
  --image-family=ubuntu-2404-lts-amd64 --image-project=ubuntu-os-cloud \
  --boot-disk-size=20GB --boot-disk-type=pd-standard \
  --disk=name=scan2work-data,device-name=scan2work-data,mode=rw,boot=no,auto-delete=no \
  --subnet=scan2work-subnet --address=scan2work-origin --network-tier=STANDARD \
  --tags=scan2work-web,scan2work-ssh --service-account="$RUNTIME_SA" \
  --scopes=https://www.googleapis.com/auth/devstorage.read_write \
  --metadata="enable-oslogin=TRUE,block-project-ssh-keys=TRUE,serial-port-enable=FALSE,scan2work-backup-bucket=$BACKUP_BUCKET" \
  --metadata-from-file="startup-script=$SCRIPT_DIR/startup.sh" \
  --shielded-secure-boot --shielded-vtpm --shielded-integrity-monitoring \
  --provisioning-model=STANDARD --maintenance-policy=MIGRATE --restart-on-failure --deletion-protection \
  --labels=app=scan2work,role=demo-origin
if [[ -n "$BUDGET_AMOUNT" ]]; then
  run "${GCLOUD[@]}" services enable billingbudgets.googleapis.com
  PROJECT_NUMBER=PROJECT_NUMBER
  if [[ "$APPLY" == 1 ]]; then PROJECT_NUMBER=$(gcloud projects describe "$PROJECT_ID" --format='value(projectNumber)'); fi
  run gcloud --quiet --project="$PROJECT_ID" --billing-project="$PROJECT_ID" billing budgets create --billing-account="$BILLING_ACCOUNT" \
    --display-name='Scan2Work monthly alert (not a cap)' --budget-amount="${BUDGET_AMOUNT}${BUDGET_CURRENCY}" \
    --filter-projects="projects/$PROJECT_NUMBER" \
    --threshold-rule=percent=0.5,basis=current-spend --threshold-rule=percent=0.8,basis=current-spend \
    --threshold-rule=percent=1.0,basis=current-spend
fi
printf '\nInfrastructure only. App installation, TLS/DNS, secret transfer and public routing remain separate.\n'
printf 'Check startup before installing: gcloud --project=%s compute ssh scan2work-cpu --zone=%s --tunnel-through-iap\n' "$PROJECT_ID" "$ZONE"
