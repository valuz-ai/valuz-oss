import { activeProfile } from '../edition'
import type { FeatureFlags } from '../edition/profile'

export const FEATURES: FeatureFlags = activeProfile.features
