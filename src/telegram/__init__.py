"""
Telegram VIP membership subscription system.

Module structure:
- database/: Database access layer (DAO)
- repositories/: Repository layer
- services/: Service layer (MemberService etc.)
- payment/: BSC payment listener
- bot/: Telegram Bot handlers
- access_controller.py: Unified access control (Channel + Group)
- vip_signal_sender.py: VIP signal push

Version: v4.0.0 (Repository/Service architecture)
"""
