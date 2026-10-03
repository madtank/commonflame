"""
User Credits Model - The operator's Minimal Gamification
Simple game credits system for demonstrating ROI to investors
NO core system changes - just gamification layer on top
"""
from sqlalchemy import Column, String, Integer, DateTime, Boolean, Text, UUID
from sqlalchemy.sql import func
from sqlalchemy.orm import relationship
from ..core.database import Base
import uuid


class UserCredits(Base):
    """User credits for The operator's beta gamification"""
    __tablename__ = "user_credits"

    # Primary fields
    user_id = Column(UUID(as_uuid=True), primary_key=True)
    credits = Column(Integer, default=0, nullable=False)
    earned_total = Column(Integer, default=0, nullable=False)
    spent_total = Column(Integer, default=0, nullable=False)

    # Activity tracking
    last_activity = Column(DateTime(timezone=True), server_default=func.now())
    last_reward = Column(DateTime(timezone=True), nullable=True)

    # Streak tracking
    daily_streak = Column(Integer, default=0)
    weekly_streak = Column(Integer, default=0)
    best_streak = Column(Integer, default=0)

    # Timestamps
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())


class CreditTransaction(Base):
    """Credit transaction history for transparency"""
    __tablename__ = "credit_transactions"

    # Primary fields
    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    user_id = Column(UUID(as_uuid=True), nullable=False)

    # Transaction details
    amount = Column(Integer, nullable=False)  # Positive = earned, Negative = spent
    transaction_type = Column(String(50), nullable=False)  # 'task_complete', 'collaboration', etc.
    description = Column(Text)

    # Context
    related_id = Column(String(255), nullable=True)  # task_id, agent_id, etc.
    extra_data = Column(Text, nullable=True)  # JSON string for additional data

    # Timestamps
    created_at = Column(DateTime(timezone=True), server_default=func.now())


class Achievement(Base):
    """User achievements for gamification"""
    __tablename__ = "user_achievements"

    # Primary fields
    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    user_id = Column(UUID(as_uuid=True), nullable=False)

    # Achievement details
    achievement_type = Column(String(100), nullable=False)
    achievement_name = Column(String(255), nullable=False)
    description = Column(Text)

    # Rewards
    credits_awarded = Column(Integer, default=0)

    # Status
    unlocked = Column(Boolean, default=True)

    # Timestamps
    earned_at = Column(DateTime(timezone=True), server_default=func.now())
