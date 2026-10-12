"""通知通道适配层（邮件 / 群机器人 Webhook / 预留通道）。

与 app/adapters/sms 的「单选 provider」不同：通知按「受众 → 通道名单」并行分发；
本层只提供单通道能力与状态描述，路由、开关与频控见 app/services/notification_service.py。
"""
