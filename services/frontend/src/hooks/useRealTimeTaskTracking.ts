import { useState, useEffect, useMemo } from 'react'

interface TaskClaim {
  taskId: string
  claimedBy: string
  claimedAt: Date
  status: 'available' | 'claimed' | 'completed'
  expiresAt?: Date
}

interface TaskFromMessage {
  taskId: string
  createdBy: string
  createdAt: Date
}

export function useRealTimeTaskTracking(posts: any[]) {
  const [tasks, setTasks] = useState<Map<string, TaskClaim>>(new Map())

  // Parse posts for task patterns in real-time
  const parsedTasks = useMemo(() => {
    const taskMap = new Map<string, TaskClaim>()

    // Sort posts by timestamp to process in chronological order
    const sortedPosts = [...posts].sort((a, b) =>
      new Date(a.uploaded_at).getTime() - new Date(b.uploaded_at).getTime()
    )

    sortedPosts.forEach(post => {
      const content = post.content
      const agent = post.agent_username
      const timestamp = new Date(post.uploaded_at)

      // Pattern 1: Task creation - "Available tasks: TASK-XXX, TASK-YYY"
      const taskCreationMatches = content.match(/Available tasks?:\s*((?:TASK-[A-Z0-9-]+(?:,\s*)?)+)/gi)
      if (taskCreationMatches) {
        taskCreationMatches.forEach(match => {
          const taskIds = match.replace(/Available tasks?:\s*/i, '').split(/,\s*/)
          taskIds.forEach(taskId => {
            const cleanTaskId = taskId.trim()
            if (cleanTaskId.startsWith('TASK-')) {
              taskMap.set(cleanTaskId, {
                taskId: cleanTaskId,
                claimedBy: '',
                claimedAt: timestamp,
                status: 'available',
                expiresAt: undefined
              })
            }
          })
        })
      }

      // Pattern 2: Task claiming - "I'll take TASK-XXX" or "I claim TASK-XXX"
      const claimPatterns = [
        /I'll take (TASK-[A-Z0-9-]+)/gi,
        /I claim (TASK-[A-Z0-9-]+)/gi,
        /claiming (TASK-[A-Z0-9-]+)/gi,
        /taking (TASK-[A-Z0-9-]+)/gi
      ]

      claimPatterns.forEach(pattern => {
        const matches = content.matchAll(pattern)
        for (const match of matches) {
          const taskId = match[1]
          const existingTask = taskMap.get(taskId)

          if (existingTask && existingTask.status === 'available') {
            // Task claimed - set 2 hour expiration
            const expiresAt = new Date(timestamp.getTime() + 2 * 60 * 60 * 1000)
            taskMap.set(taskId, {
              ...existingTask,
              claimedBy: agent,
              claimedAt: timestamp,
              status: 'claimed',
              expiresAt
            })
          } else if (!existingTask) {
            // New task created by claiming (sometimes agents claim tasks not formally announced)
            const expiresAt = new Date(timestamp.getTime() + 2 * 60 * 60 * 1000)
            taskMap.set(taskId, {
              taskId,
              claimedBy: agent,
              claimedAt: timestamp,
              status: 'claimed',
              expiresAt
            })
          }
        }
      })

      // Pattern 3: Task completion - "Completed TASK-XXX" or "Finished TASK-XXX"
      const completionPatterns = [
        /(?:Completed|Finished|Done with)\s+(TASK-[A-Z0-9-]+)/gi,
        /(TASK-[A-Z0-9-]+)\s+(?:completed|finished|done)/gi
      ]

      completionPatterns.forEach(pattern => {
        const matches = content.matchAll(pattern)
        for (const match of matches) {
          const taskId = match[1]
          const existingTask = taskMap.get(taskId)

          if (existingTask) {
            taskMap.set(taskId, {
              ...existingTask,
              status: 'completed',
              expiresAt: undefined
            })
          }
        }
      })
    })

    return taskMap
  }, [posts])

  // Update state when parsed tasks change
  useEffect(() => {
    setTasks(parsedTasks)
  }, [parsedTasks])

  // Check for expired tasks
  useEffect(() => {
    const interval = setInterval(() => {
      const now = new Date()
      setTasks(prev => {
        const updated = new Map(prev)
        let hasChanges = false

        for (const [taskId, task] of updated.entries()) {
          if (task.status === 'claimed' && task.expiresAt && now > task.expiresAt) {
            updated.set(taskId, {
              ...task,
              status: 'available',
              claimedBy: '',
              expiresAt: undefined
            })
            hasChanges = true
          }
        }

        return hasChanges ? updated : prev
      })
    }, 10000) // Check every 10 seconds

    return () => clearInterval(interval)
  }, [])

  return {
    tasks: Array.from(tasks.values()),
    getTaskStatus: (taskId: string) => tasks.get(taskId),
    taskCount: {
      available: Array.from(tasks.values()).filter(t => t.status === 'available').length,
      claimed: Array.from(tasks.values()).filter(t => t.status === 'claimed').length,
      completed: Array.from(tasks.values()).filter(t => t.status === 'completed').length
    }
  }
}
