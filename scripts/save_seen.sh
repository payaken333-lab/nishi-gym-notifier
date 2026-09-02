#!/bin/bash
set -e

git config user.name "github-actions[bot]"
git config user.email "github-actions[bot]@users.noreply.github.com"
git add seen_slots.json

if git diff --cached --quiet; then
  echo "変更なし"
  exit 0
fi

git commit -m "chore: update seen slots"

for i in 1 2 3 4 5; do
  git fetch origin main
  if git rebase origin/main && git push origin HEAD:main; then
    echo "保存成功"
    exit 0
  fi
  echo "競合が発生したためリトライします (${i}回目)"
  git rebase --abort || true
  sleep $((RANDOM % 5 + 1))
done

echo "保存に失敗しました"
exit 1
