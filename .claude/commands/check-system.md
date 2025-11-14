Check overall system health and provide a status summary.

Steps to execute:
1. Check Docker container status (docker-compose ps)
2. Review recent collector log entries (last 10-20 lines)
3. Query database for recent activity:
   - Total runway configs collected
   - Configs from last 10 minutes
   - Total runway changes detected
4. Check review queue statistics
5. Verify API is responding (curl health endpoint)

Provide a concise summary including:
- Container health status (all running/healthy?)
- Recent collection activity (is collector working?)
- Database stats (data flowing in?)
- Review queue size (items pending human review)
- Any errors or concerns detected
- Overall system health assessment (healthy/degraded/down)
