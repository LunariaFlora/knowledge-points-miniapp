#!/usr/bin/env bash
set -euo pipefail

container_name="knowledge-points-test-app-1"
backup_dir="/home/ecs-user/knowledge-points-backups"
timestamp="$(date +%Y%m%d-%H%M%S)"
filename="knowledge-points-${timestamp}.sqlite3"
container_file="/app/database/backups/${filename}"
oss_key="knowledge-points/backups/database/${filename}"

mkdir -p "${backup_dir}"
test "$(realpath "${backup_dir}")" = "/home/ecs-user/knowledge-points-backups"

sudo docker exec "${container_name}" python app/backup.py --output "${container_file}" --oss-key "${oss_key}" >/dev/null
sudo docker cp "${container_name}:${container_file}" "${backup_dir}/${filename}" >/dev/null
sudo docker exec "${container_name}" rm -f -- "${container_file}"

echo "${backup_dir}/${filename}"
echo "private OSS object: ${oss_key}"
