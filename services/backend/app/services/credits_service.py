"""
Credits Service - The operator's Minimal Gamification System
Legally safe game credits (NOT cryptocurrency) for beta demonstration
"""
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, update, and_
from sqlalchemy.sql import func
from typing import Optional, Dict, List
from datetime import datetime, timedelta
import json

from ..models.credits import UserCredits, CreditTransaction, Achievement
from ..models.user import User


class CreditsService:
    """Service for managing user credits and achievements"""

    # The operator's Credit Reward Structure (From Jacob_Minimal_Gamified_Beta_Plan.md)
    REWARDS = {
        "task_complete": 10,
        "agent_collaboration": 15,
        "multi_agent_collaboration": 25,
        "help_user": 15,
        "create_task": 20,
        "refer_user": 50,
        "first_agent": 25,
        "first_collaboration": 30,
        "daily_activity": 5,
        "weekly_champion": 100
    }

    @classmethod
    async def get_or_create_user_credits(cls, db: AsyncSession, user_id: str) -> UserCredits:
        """Get user credits or create if not exists"""
        result = await db.execute(
            select(UserCredits).where(UserCredits.user_id == user_id)
        )
        user_credits = result.scalar_one_or_none()

        if not user_credits:
            user_credits = UserCredits(user_id=user_id)
            db.add(user_credits)
            await db.commit()
            await db.refresh(user_credits)

        return user_credits

    @classmethod
    async def award_credits(
        cls,
        db: AsyncSession,
        user_id: str,
        amount: int,
        transaction_type: str,
        description: str = None,
        related_id: str = None,
        metadata: dict = None
    ) -> UserCredits:
        """Award credits to user and record transaction"""

        # Get or create user credits
        user_credits = await cls.get_or_create_user_credits(db, user_id)

        # Update credits
        user_credits.credits += amount
        user_credits.earned_total += amount
        user_credits.last_activity = datetime.utcnow()

        if transaction_type in ['task_complete', 'agent_collaboration']:
            user_credits.last_reward = datetime.utcnow()

        # Record transaction
        transaction = CreditTransaction(
            user_id=user_id,
            amount=amount,
            transaction_type=transaction_type,
            description=description or f"Earned {amount} credits for {transaction_type}",
            related_id=related_id,
            metadata=json.dumps(metadata) if metadata else None
        )

        db.add(transaction)
        await db.commit()
        await db.refresh(user_credits)

        return user_credits

    @classmethod
    async def award_achievement(
        cls,
        db: AsyncSession,
        user_id: str,
        achievement_type: str,
        achievement_name: str,
        description: str,
        credits_awarded: int = 0
    ) -> Achievement:
        """Award achievement to user"""

        # Check if user already has this achievement
        result = await db.execute(
            select(Achievement).where(
                and_(
                    Achievement.user_id == user_id,
                    Achievement.achievement_type == achievement_type
                )
            )
        )
        existing = result.scalar_one_or_none()

        if existing:
            return existing  # Already has this achievement

        # Create new achievement
        achievement = Achievement(
            user_id=user_id,
            achievement_type=achievement_type,
            achievement_name=achievement_name,
            description=description,
            credits_awarded=credits_awarded
        )

        db.add(achievement)

        # Award credits if specified
        if credits_awarded > 0:
            await cls.award_credits(
                db, user_id, credits_awarded,
                f"achievement_{achievement_type}",
                f"Achievement unlocked: {achievement_name}"
            )

        await db.commit()
        await db.refresh(achievement)

        return achievement

    @classmethod
    async def get_user_stats(cls, db: AsyncSession, user_id: str) -> Dict:
        """Get comprehensive user stats for leaderboard"""
        user_credits = await cls.get_or_create_user_credits(db, user_id)

        # Get recent transactions
        transactions_result = await db.execute(
            select(CreditTransaction)
            .where(CreditTransaction.user_id == user_id)
            .order_by(CreditTransaction.created_at.desc())
            .limit(10)
        )
        recent_transactions = transactions_result.scalars().all()

        # Get achievements
        achievements_result = await db.execute(
            select(Achievement)
            .where(Achievement.user_id == user_id)
            .order_by(Achievement.earned_at.desc())
        )
        achievements = achievements_result.scalars().all()

        return {
            "credits": user_credits.credits,
            "earned_total": user_credits.earned_total,
            "spent_total": user_credits.spent_total,
            "daily_streak": user_credits.daily_streak,
            "weekly_streak": user_credits.weekly_streak,
            "best_streak": user_credits.best_streak,
            "last_activity": user_credits.last_activity,
            "recent_transactions": [
                {
                    "amount": t.amount,
                    "type": t.transaction_type,
                    "description": t.description,
                    "created_at": t.created_at
                } for t in recent_transactions
            ],
            "achievements": [
                {
                    "type": a.achievement_type,
                    "name": a.achievement_name,
                    "description": a.description,
                    "credits": a.credits_awarded,
                    "earned_at": a.earned_at
                } for a in achievements
            ]
        }

    @classmethod
    async def get_leaderboard(cls, db: AsyncSession, limit: int = 10) -> List[Dict]:
        """Get top users by credits for leaderboard"""
        result = await db.execute(
            select(UserCredits, User)
            .join(User, UserCredits.user_id == User.id)
            .order_by(UserCredits.credits.desc())
            .limit(limit)
        )

        leaderboard = []
        for user_credits, user in result:
            leaderboard.append({
                "user_id": str(user.id),
                "username": user.username,
                "full_name": user.full_name,
                "credits": user_credits.credits,
                "earned_total": user_credits.earned_total,
                "daily_streak": user_credits.daily_streak,
                "last_activity": user_credits.last_activity
            })

        return leaderboard

    @classmethod
    async def check_and_award_streak_bonus(cls, db: AsyncSession, user_id: str):
        """Check and award daily/weekly streak bonuses"""
        user_credits = await cls.get_or_create_user_credits(db, user_id)
        now = datetime.utcnow()

        # Check daily streak
        if user_credits.last_activity:
            time_diff = now - user_credits.last_activity
            if time_diff.days == 1:  # Exactly one day
                user_credits.daily_streak += 1
                user_credits.best_streak = max(user_credits.best_streak, user_credits.daily_streak)

                # Award streak bonus
                if user_credits.daily_streak % 7 == 0:  # Weekly streak
                    await cls.award_credits(
                        db, user_id, cls.REWARDS["weekly_champion"],
                        "weekly_streak",
                        f"Weekly streak bonus! {user_credits.daily_streak} days"
                    )
            elif time_diff.days > 1:  # Streak broken
                user_credits.daily_streak = 1
        else:
            user_credits.daily_streak = 1

        user_credits.last_activity = now
        await db.commit()


# Convenience functions for common credit awards
async def award_task_completion(db: AsyncSession, user_id: str, task_id: str):
    """Award credits for task completion"""
    await CreditsService.award_credits(
        db, user_id, CreditsService.REWARDS["task_complete"],
        "task_complete", f"Task completed", task_id
    )

async def award_agent_collaboration(db: AsyncSession, user_id: str, agent_id: str):
    """Award credits for agent collaboration"""
    await CreditsService.award_credits(
        db, user_id, CreditsService.REWARDS["agent_collaboration"],
        "agent_collaboration", f"Agent collaboration", agent_id
    )

async def award_first_agent(db: AsyncSession, user_id: str, agent_id: str):
    """Award achievement for first agent registration"""
    await CreditsService.award_achievement(
        db, user_id, "first_agent", "First Agent",
        "Registered your first agent!", CreditsService.REWARDS["first_agent"]
    )
