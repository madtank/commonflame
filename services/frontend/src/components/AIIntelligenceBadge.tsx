/**
 * AI Intelligence Badge
 * Displays AI-analyzed quality scores for messages
 * Shows a subtle visual indicator with detailed tooltip on hover
 */

import React, { useState } from 'react';
import { Shield, ShieldAlert, ShieldCheck, Sparkles } from 'lucide-react';

export interface IntelligenceScores {
  spam_score: number;      // 0.0 - 1.0
  toxicity_score: number;  // 0.0 - 1.0
  quality_score: number;   // 0.0 - 1.0
  ai_reactions?: string[]; // Emojis added by AI
}

interface AIIntelligenceBadgeProps {
  scores?: IntelligenceScores | null;
  showDetails?: boolean;   // Show expanded view
  compact?: boolean;       // Minimal view for inline use
  className?: string;
}

// Determine overall trust level from scores
const getTrustLevel = (scores: IntelligenceScores): 'high' | 'medium' | 'low' | 'warning' => {
  if (scores.spam_score > 0.7 || scores.toxicity_score > 0.7) return 'warning';
  if (scores.quality_score >= 0.8 && scores.spam_score < 0.2 && scores.toxicity_score < 0.2) return 'high';
  if (scores.quality_score >= 0.5) return 'medium';
  return 'low';
};

// Visual config for each trust level
const trustConfig = {
  high: {
    icon: ShieldCheck,
    color: 'text-emerald-500',
    bg: 'bg-emerald-500/10',
    border: 'border-emerald-500/30',
    label: 'High Quality',
    description: 'AI analysis indicates high-quality, helpful content',
  },
  medium: {
    icon: Shield,
    color: 'text-blue-500',
    bg: 'bg-blue-500/10',
    border: 'border-blue-500/30',
    label: 'Good',
    description: 'AI analysis indicates standard quality content',
  },
  low: {
    icon: Shield,
    color: 'text-gray-400',
    bg: 'bg-gray-500/10',
    border: 'border-gray-500/30',
    label: 'Neutral',
    description: 'AI analysis shows neutral quality indicators',
  },
  warning: {
    icon: ShieldAlert,
    color: 'text-amber-500',
    bg: 'bg-amber-500/10',
    border: 'border-amber-500/30',
    label: 'Review',
    description: 'AI flagged potential issues - may need review',
  },
};

export const AIIntelligenceBadge: React.FC<AIIntelligenceBadgeProps> = ({
  scores,
  showDetails = false,
  compact = false,
  className = '',
}) => {
  const [isHovered, setIsHovered] = useState(false);

  // Don't render if no scores
  if (!scores) return null;

  const trustLevel = getTrustLevel(scores);
  const config = trustConfig[trustLevel];
  const Icon = config.icon;

  // Score bar component
  const ScoreBar = ({ label, value, color }: { label: string; value: number; color: string }) => (
    <div className="flex items-center gap-2 text-xs">
      <span className="w-16 text-gray-500 dark:text-gray-400">{label}</span>
      <div className="flex-1 h-1.5 bg-gray-200 dark:bg-gray-700 rounded-full overflow-hidden">
        <div
          className={`h-full rounded-full transition-all duration-300 ${color}`}
          style={{ width: `${Math.round(value * 100)}%` }}
        />
      </div>
      <span className="w-8 text-right text-gray-500 dark:text-gray-400">
        {Math.round(value * 100)}%
      </span>
    </div>
  );

  // Compact inline badge
  if (compact) {
    return (
      <div
        className={`inline-flex items-center gap-1 cursor-pointer ${className}`}
        onMouseEnter={() => setIsHovered(true)}
        onMouseLeave={() => setIsHovered(false)}
        onFocus={() => setIsHovered(true)}
        onBlur={() => setIsHovered(false)}
        tabIndex={0}
        role="button"
        aria-label={`AI quality: ${config.label}`}
      >
        <Icon className={`w-3.5 h-3.5 ${config.color}`} />
        {isHovered && (
          <span className={`text-xs ${config.color}`}>
            {config.label}
          </span>
        )}
      </div>
    );
  }

  return (
    <div
      className={`relative ${className}`}
      onMouseEnter={() => setIsHovered(true)}
      onMouseLeave={() => setIsHovered(false)}
      onFocus={() => setIsHovered(true)}
      onBlur={() => setIsHovered(false)}
      tabIndex={0}
    >
      {/* Badge trigger */}
      <div
        className={`
          inline-flex items-center gap-1.5 px-2 py-1 rounded-full text-xs font-medium
          transition-all duration-200 cursor-default
          ${config.bg} ${config.color} border ${config.border}
        `}
      >
        <Icon className="w-3.5 h-3.5" />
        <span>{config.label}</span>
        {scores.ai_reactions && scores.ai_reactions.length > 0 && (
          <span className="ml-0.5 opacity-75">
            {scores.ai_reactions.slice(0, 2).join('')}
          </span>
        )}
      </div>

      {/* Tooltip on hover */}
      {(isHovered || showDetails) && (
        <div
          className={`
            absolute z-50 bottom-full left-0 mb-2 w-64 p-3 rounded-lg shadow-xl
            bg-white dark:bg-gray-800 border border-gray-200 dark:border-gray-700
            animate-in fade-in slide-in-from-bottom-1 duration-150
          `}
        >
          {/* Header */}
          <div className="flex items-center gap-2 mb-3 pb-2 border-b border-gray-100 dark:border-gray-700">
            <Sparkles className="w-4 h-4 text-indigo-500" />
            <span className="text-sm font-medium text-gray-900 dark:text-gray-100">
              AI Analysis
            </span>
          </div>

          {/* Scores */}
          <div className="space-y-2">
            <ScoreBar
              label="Quality"
              value={scores.quality_score}
              color={scores.quality_score >= 0.7 ? 'bg-emerald-500' : scores.quality_score >= 0.4 ? 'bg-blue-500' : 'bg-gray-400'}
            />
            <ScoreBar
              label="Spam"
              value={scores.spam_score}
              color={scores.spam_score > 0.5 ? 'bg-red-500' : scores.spam_score > 0.3 ? 'bg-amber-500' : 'bg-emerald-500'}
            />
            <ScoreBar
              label="Toxicity"
              value={scores.toxicity_score}
              color={scores.toxicity_score > 0.5 ? 'bg-red-500' : scores.toxicity_score > 0.3 ? 'bg-amber-500' : 'bg-emerald-500'}
            />
          </div>

          {/* AI Reactions */}
          {scores.ai_reactions && scores.ai_reactions.length > 0 && (
            <div className="mt-3 pt-2 border-t border-gray-100 dark:border-gray-700">
              <div className="flex items-center gap-2">
                <span className="text-xs text-gray-500 dark:text-gray-400">AI reactions:</span>
                <div className="flex gap-1">
                  {scores.ai_reactions.map((emoji, i) => (
                    <span key={i} className="text-sm">{emoji}</span>
                  ))}
                </div>
              </div>
            </div>
          )}

          {/* Footer description */}
          <p className="mt-3 text-xs text-gray-500 dark:text-gray-400 leading-relaxed">
            {config.description}
          </p>
        </div>
      )}
    </div>
  );
};

export default AIIntelligenceBadge;
