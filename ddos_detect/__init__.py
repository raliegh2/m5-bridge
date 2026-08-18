"""ddos_detect - a defensive DDoS detection and monitoring system.

Scope and intent
----------------
This package is *detection only*. It observes traffic that already reaches a
network interface the operator controls, computes statistical and rule-based
signals, and raises alerts. It contains no traffic-generation, amplification,
scanning, or mitigation-by-attack capability.

Every monitored target must first be recorded in the authorization ledger
(:mod:`ddos_detect.authz`) together with an operator attestation, and every such
action is written to a tamper-evident audit log (:mod:`ddos_detect.audit`).
"""

__version__ = "1.0.0"
__all__ = ["__version__"]
