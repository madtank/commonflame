import { describe, it, expect } from 'vitest'
import { FOLLOW_UUID, getMobilityMode } from './agent-mobility'

describe('agent-mobility helpers', () => {
  it('detects free roam by default', () => {
    expect(getMobilityMode({})).toBe('free')
  })

  it('detects pinned when pinned_to_org present', () => {
    expect(getMobilityMode({ pinned_to_org: 'abc' })).toBe('pinned')
  })

  it('detects follow when pinned_to_org is FOLLOW_UUID', () => {
    expect(getMobilityMode({ pinned_to_org: FOLLOW_UUID })).toBe('follow')
  })

  it('detects follow when org_id is FOLLOW_UUID (edge case)', () => {
    expect(getMobilityMode({ org_id: FOLLOW_UUID })).toBe('follow')
  })

  it('detects follow when explicit flag set', () => {
    expect(getMobilityMode({ follow_user: true })).toBe('follow')
    expect(getMobilityMode({ settings: { follow_user: true } })).toBe('follow')
  })
})
