#!/bin/bash
# TrendiaTR Cluster Quality Monitor
# Runs every hour for 7 iterations, then writes final report.

REPORT_FILE="/home/ubuntu/projects/turkey-trend-watcher/cluster_report.md"
STATE_FILE="/tmp/ttw_monitor_state"
LOG_DIR="/home/ubuntu/projects/turkey-trend-watcher"

# --- Iteration tracking ---
if [ -f "$STATE_FILE" ]; then
    ITERATION=$(cat "$STATE_FILE")
    ITERATION=$((ITERATION + 1))
else
    ITERATION=1
    # Initialize report file
    echo "# گزارش ۷ ساعته کیفیت کلاسترها" > "$REPORT_FILE"
    echo "شروع: $(date -u '+%Y-%m-%d %H:%M UTC')" >> "$REPORT_FILE"
    echo "" >> "$REPORT_FILE"
    echo "**Baseline:** 219 کلاستر فعال" >> "$REPORT_FILE"
    echo "**تغییرات اعمال‌شده:**" >> "$REPORT_FILE"
    echo "- uncertain_thresh RSS: 0.42 → 0.35" >> "$REPORT_FILE"
    echo "- X-Trend auto_merge: 0.22 → 0.12 | uncertain: 0.42 → 0.20" >> "$REPORT_FILE"
    echo "- merge_worker Gemini: gemini-2.5-flash-lite + thinking_budget=0" >> "$REPORT_FILE"
    echo "" >> "$REPORT_FILE"
    echo "---" >> "$REPORT_FILE"
fi
echo "$ITERATION" > "$STATE_FILE"

TIMESTAMP=$(date -u '+%H:%M UTC')

# --- DB shortcut ---
DB="sudo docker exec ttw_postgres psql -U admin -d trend_watcher_db -t -A -F'|' -c"

# ---- 1. New clusters this hour ----
NEW_CLUSTERS=$($DB "
SELECT id, LEFT(title,60), category, message_count, ROUND(final_tps::numeric,1)
FROM trends
WHERE is_active=true AND first_seen > now() - interval '1 hour'
ORDER BY first_seen DESC LIMIT 20;" 2>/dev/null)
NEW_COUNT=$(echo "$NEW_CLUSTERS" | grep -c '|')

# ---- 2. Total active ----
TOTAL=$($DB "SELECT COUNT(*) FROM trends WHERE is_active=true;" 2>/dev/null | tr -d ' ')

# ---- 3. Large clusters (>15 news) ----
LARGE=$($DB "
SELECT id, LEFT(title,50), message_count
FROM trends
WHERE is_active=true AND message_count > 15 AND first_seen > now() - interval '7 hours'
ORDER BY message_count DESC LIMIT 5;" 2>/dev/null)
LARGE_COUNT=$(echo "$LARGE" | grep -c '|')

# ---- 4. X-Trend mixed with RSS (critical check) ----
MIXED=$($DB "
SELECT t.id, LEFT(t.title,50), t.message_count,
  COUNT(*) FILTER (WHERE r.source_type='x') as x_cnt,
  COUNT(*) FILTER (WHERE r.source_type!='x') as other_cnt
FROM trends t
JOIN raw_news r ON r.trend_id = t.id
WHERE t.is_active=true AND t.first_seen > now() - interval '7 hours'
GROUP BY t.id, t.title, t.message_count
HAVING COUNT(*) FILTER (WHERE r.source_type='x') > 0
   AND COUNT(*) FILTER (WHERE r.source_type!='x') > 0
ORDER BY t.message_count DESC LIMIT 5;" 2>/dev/null)
MIXED_COUNT=$(echo "$MIXED" | grep -c '|')

# ---- 5. Orphaned news ----
ORPHANED=$($DB "
SELECT COUNT(*) FROM raw_news
WHERE trend_id IS NULL AND created_at > now() - interval '7 hours';" 2>/dev/null | tr -d ' ')

# ---- 6. merge_worker status ----
MERGE_STATUS=$(sudo docker logs ttw_merge --tail 8 2>&1 | grep -E "Cycle done|merges performed|Next cycle|ERROR" | tail -3)

# ---- 7. RSS health ----
RSS_STATUS=$(sudo docker logs ttw_rss --tail 6 2>&1 | grep -E "RSS Cycle|New Trends|Signal Updates|ERROR" | tail -2)

# ---- Assessment ----
if [ "$MIXED_COUNT" -gt 0 ]; then
    ASSESSMENT="🔴 مشکل: X-Trend با RSS ترکیب شده"
elif [ "$LARGE_COUNT" -gt 0 ]; then
    ASSESSMENT="⚠️ هشدار: کلاستر بزرگ یافت شد"
else
    ASSESSMENT="✅ طبیعی"
fi

# ---- Write to report ----
cat >> "$REPORT_FILE" << EOF

### Iteration $ITERATION/7 — $TIMESTAMP
- **کلاسترهای جدید این ساعت:** $NEW_COUNT
- **کل کلاسترهای فعال:** $TOTAL
- **کلاسترهای بزرگ (>15 خبر):** $LARGE_COUNT
- **X-Trend مخلوط با RSS:** $MIXED_COUNT
- **اخبار بدون کلاستر (۷ ساعت اخیر):** $ORPHANED
- **merge_worker:** $(echo "$MERGE_STATUS" | tail -1 | cut -c1-100)
- **RSS worker:** $(echo "$RSS_STATUS" | tail -1 | cut -c1-100)
- **ارزیابی:** $ASSESSMENT

**کلاسترهای جدید این ساعت:**
$(echo "$NEW_CLUSTERS" | awk -F'|' '{printf "  - [%s] %s (%s) | %s خبر | TPS:%s\n",$3,$2,$1,$4,$5}' | head -15)

---
EOF

echo "✅ Iteration $ITERATION/7 written to $REPORT_FILE"

# ---- Final report after iteration 7 ----
if [ "$ITERATION" -ge 7 ]; then
    FINAL_TIME=$(date -u '+%Y-%m-%d %H:%M UTC')

    # Summary stats
    TOTAL_CREATED=$($DB "
    SELECT COUNT(*) FROM trends
    WHERE is_active=true AND first_seen > now() - interval '7 hours';" 2>/dev/null | tr -d ' ')

    AVG_SIZE=$($DB "
    SELECT ROUND(AVG(message_count)::numeric, 1) FROM trends
    WHERE is_active=true AND first_seen > now() - interval '7 hours';" 2>/dev/null | tr -d ' ')

    MAX_SIZE=$($DB "
    SELECT MAX(message_count) FROM trends
    WHERE is_active=true AND first_seen > now() - interval '7 hours';" 2>/dev/null | tr -d ' ')

    TOTAL_MIXED=$($DB "
    SELECT COUNT(*) FROM (
      SELECT t.id FROM trends t
      JOIN raw_news r ON r.trend_id = t.id
      WHERE t.is_active=true AND t.first_seen > now() - interval '7 hours'
      GROUP BY t.id
      HAVING COUNT(*) FILTER (WHERE r.source_type='x') > 0
         AND COUNT(*) FILTER (WHERE r.source_type!='x') > 0
    ) sub;" 2>/dev/null | tr -d ' ')

    MERGE_CYCLES=$(sudo docker logs ttw_merge 2>&1 | grep -c "Cycle done" || echo 0)
    MERGE_MERGES=$(sudo docker logs ttw_merge 2>&1 | grep "merges performed" | grep -v " 0 merges" | wc -l || echo 0)

    cat >> "$REPORT_FILE" << FINALEOF

---

## گزارش نهایی — $FINAL_TIME

### ۱. خلاصه کلی
- **کل کلاسترهای جدید (۷ ساعت):** $TOTAL_CREATED
- **میانگین اندازه کلاستر:** $AVG_SIZE خبر
- **بزرگ‌ترین کلاستر:** $MAX_SIZE خبر
- **نتیجه:** $([ "$TOTAL_MIXED" -eq 0 ] && echo "✅ threshold‌های جدید کار کردند" || echo "⚠️ $TOTAL_MIXED کلاستر مشکل‌دار")

### ۲. X-Trend Isolation
- **تعداد کلاسترهای مخلوط X+RSS:** $TOTAL_MIXED
- $([ "$TOTAL_MIXED" -eq 0 ] && echo "✅ X-Trend با اخبار RSS ترکیب نشد — threshold 0.12 کار کرد" || echo "🔴 مشکل: X-Trend با RSS ترکیب شده")

### ۳. merge_worker
- **تعداد سیکل‌های اجراشده:** $MERGE_CYCLES
- **سیکل‌هایی با merge موفق:** $MERGE_CYCLES

### ۴. وضعیت کلی سیستم
$(sudo docker ps --format "{{.Names}}: {{.Status}}" | grep ttw_ | grep -v "12 days\|11 days" | head -12)

### ۵. توصیه
$([ "$TOTAL_MIXED" -eq 0 ] && echo "- threshold‌های فعلی مناسب به نظر می‌رسند. نگه داشته شوند." || echo "- X-Trend threshold را به 0.08 کاهش دهید.")
- merge_worker را هر ۱ ساعت (به جای ۲ ساعت) اجرا کنید.
FINALEOF

    echo "📊 Final report written!"
    # Remove cron job after completion
    crontab -l 2>/dev/null | grep -v "monitor_clusters.sh" | crontab -
    echo "🗑️ Cron job removed after 7 iterations."
    rm -f "$STATE_FILE"
fi
