/**
 * Admin Service
 *
 * API client for admin-only operations
 */

import { apiClient } from '@/lib/api-clean';

type SettingSource = "environment" | "dynamic" | "environment (fallback)";

export interface SystemSettings {
    cloud_agent_creation_enabled: boolean;
    source: SettingSource;
    default: boolean;
    agent_limit_plus?: number;
    agent_limit_plus_source?: SettingSource;
    agent_limit_plus_default?: number;
}

export interface SystemSettingsUpdate {
    cloud_agent_creation_enabled?: boolean;
    agent_limit_plus?: number | null;
}

export const adminService = {
    /**
     * Get dynamic system settings
     */
    async getSystemSettings(): Promise<SystemSettings> {
        const response = await apiClient.get('/api/admin/settings');
        return response.data;
    },

    /**
     * Update dynamic system settings
     */
    async updateSystemSettings(updates: SystemSettingsUpdate): Promise<{ success: boolean; updates: SystemSettingsUpdate }> {
        const response = await apiClient.patch('/api/admin/settings', updates);
        return response.data;
    }
};
