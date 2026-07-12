"""Offline, human-gated distillation experiment (see README.md).

Distil a cheap student model from a large prompted teacher on the UNLABELLED
corpus, then evaluate it on the untouched human ground truth with the same
production gate. Not wired into the autonomous supervisor: a fine-tuned model is
a weight artifact and must pass human review before it can enter models.yaml.
"""
