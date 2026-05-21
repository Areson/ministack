def test_rds_cluster_snapshot_filter_accepts_same_region_cluster_arn():
    from ministack.core.responses import get_account_id, get_region, set_request_account_id, set_request_region
    from ministack.services import rds as m

    original_account = get_account_id()
    original_region = get_region()
    original_clusters = dict(m._clusters._data)
    original_snapshots = dict(m._db_cluster_snapshots._data)
    cluster_arn = "arn:aws:rds:us-east-1:000000000000:cluster:snap-cl-arn"

    try:
        m._clusters.clear()
        m._db_cluster_snapshots.clear()
        set_request_account_id("000000000000")
        set_request_region("us-east-1")
        m._clusters["snap-cl-arn"] = {
            "DBClusterIdentifier": "snap-cl-arn",
            "DBClusterArn": cluster_arn,
            "Engine": "aurora-mysql",
            "EngineVersion": "8.0.mysql_aurora.3.08.0",
        }

        status, _headers, _body = m._create_db_cluster_snapshot({
            "DBClusterSnapshotIdentifier": "snap-cl-arn-snap",
            "DBClusterIdentifier": cluster_arn,
        })
        assert status == 200

        status, _headers, body = m._describe_db_cluster_snapshots({
            "DBClusterIdentifier": cluster_arn,
        })

        assert status == 200
        assert b"<DBClusterSnapshotIdentifier>snap-cl-arn-snap</DBClusterSnapshotIdentifier>" in body
    finally:
        m._clusters.clear()
        m._clusters._data.update(original_clusters)
        m._db_cluster_snapshots.clear()
        m._db_cluster_snapshots._data.update(original_snapshots)
        set_request_account_id(original_account)
        set_request_region(original_region)


def test_rds_global_cluster_writer_member_lists_secondary_readers():
    from ministack.services import rds as m

    writer_arn = "arn:aws:rds:us-east-1:000000000000:cluster:writer-cluster"
    reader_arn = "arn:aws:rds:us-west-2:000000000000:cluster:reader-cluster"
    global_cluster = {
        "GlobalClusterIdentifier": "reader-topology",
        "GlobalClusterMembers": [],
    }

    m._attach_cluster_to_global(
        global_cluster,
        {"DBClusterIdentifier": "writer-cluster", "DBClusterArn": writer_arn},
        is_writer=True,
    )
    m._attach_cluster_to_global(
        global_cluster,
        {"DBClusterIdentifier": "reader-cluster", "DBClusterArn": reader_arn},
        is_writer=False,
    )

    members = global_cluster["GlobalClusterMembers"]
    writer = next(member for member in members if member["IsWriter"])
    reader = next(member for member in members if not member["IsWriter"])
    assert writer["Readers"] == [reader_arn]
    assert reader["Readers"] == []
