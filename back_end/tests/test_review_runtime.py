from src.observability import AuditEventLog, RuntimeMetrics
import pytest


def test_audit_survives_reopen_and_redacts_credentials(tmp_path):
    path=str(tmp_path/'audit.db')
    log=AuditEventLog(db_path=path)
    log.record('auth','login','success',actor='account-a',request_id='request-1',detail={'password':'secret','account':'a'})
    reloaded=AuditEventLog(db_path=path).query()
    assert reloaded[0]['actor']=='account-a' and reloaded[0]['request_id']=='request-1'
    assert reloaded[0]['detail']['password']=='[REDACTED]'


def test_metrics_labels_are_bounded_and_escaped():
    metrics=RuntimeMetrics()
    metrics.record_http('GET','/"quoted\n',200,.1)
    for i in range(2000): metrics.record_http('GET',f'/random/{i}',404,.1)
    assert len(metrics.http_requests)<=130
    assert '\\"quoted\\n' in metrics.prometheus_text()


def test_single_instance_lock_rejects_second_owner(tmp_path):
    from src.runtime import InstanceLock
    first=InstanceLock(tmp_path/'executor.lock');second=InstanceLock(tmp_path/'executor.lock')
    first.acquire()
    try:
        with pytest.raises(RuntimeError): second.acquire()
    finally: first.release()
    second.acquire(); second.release()


def test_backup_and_restore_execution_database(tmp_path):
    from src.runtime import backup_database, restore_database
    from src.trading.ledger import ExecutionLedger
    source=tmp_path/'execution.db';backup=tmp_path/'snapshot.db';restored=tmp_path/'restored.db'
    ledger=ExecutionLedger(str(source),'a');ledger.day_baseline('20261009',100)
    backup_database(source,backup);ledger.close()
    restore_database(backup,restored)
    ledger=ExecutionLedger(str(restored),'a')
    assert ledger.day_baseline('20261009',80)==100
    ledger.close()
