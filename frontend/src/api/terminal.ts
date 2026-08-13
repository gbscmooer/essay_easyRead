import { post } from './client'

const TERMINAL_PROTOCOL_PREFIX = 'essay-terminal.'

interface TerminalSessionResponse {
  ticket: string
  expires_in: number
}

export async function createTerminalSession(signal?: AbortSignal): Promise<TerminalSessionResponse> {
  return post<TerminalSessionResponse>('/terminal/session', undefined, signal)
}

export function terminalWebSocketTarget(
  ticket: string,
  cwd: string | undefined,
  location: Pick<Location, 'protocol' | 'host'> = window.location,
): { url: string; protocol: string } {
  const wsScheme = location.protocol === 'https:' ? 'wss:' : 'ws:'
  const params = new URLSearchParams()
  if (cwd) params.set('cwd', cwd)
  const query = params.toString()
  return {
    url: `${wsScheme}//${location.host}/api/terminal/ws${query ? `?${query}` : ''}`,
    protocol: `${TERMINAL_PROTOCOL_PREFIX}${ticket}`,
  }
}
