import { useEffect, useState } from 'react'
import { getAskStatus } from '../services/ask'

/**
 * Ask PIC availability for the entry points and the page (#636).
 * `null` while unknown, then `{ enabled, max_question_chars }`.
 */
export function useAskStatus() {
    const [status, setStatus] = useState(null)

    useEffect(() => {
        let cancelled = false
        // Async IIFE: the setState lands outside the effect's synchronous
        // body (react-hooks/set-state-in-effect).
        ;(async () => {
            const result = await getAskStatus()
            if (!cancelled) setStatus(result)
        })()
        return () => {
            cancelled = true
        }
    }, [])

    return status
}
