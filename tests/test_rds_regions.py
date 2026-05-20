import uuid

import boto3
import pytest
from botocore.config import Config
from botocore.exceptions import ClientError
from conftest import ENDPOINT, make_client


def _regional_rds(region, access_key_id="test"):
    if access_key_id == "test":
        return make_client("rds", region_name=region)
    return boto3.client(
        "rds",
        endpoint_url=ENDPOINT,
        aws_access_key_id=access_key_id,
        aws_secret_access_key="test",
        region_name=region,
        config=Config(region_name=region, retries={"mode": "standard"}),
    )


def _delete_cluster(client, cluster_id):
    try:
        client.delete_db_cluster(DBClusterIdentifier=cluster_id, SkipFinalSnapshot=True)
    except ClientError:
        pass


def _remove_global_member(client, global_id, cluster_id):
    try:
        client.remove_from_global_cluster(
            GlobalClusterIdentifier=global_id,
            DbClusterIdentifier=cluster_id,
        )
    except ClientError:
        pass


def _delete_global_cluster(client, global_id):
    try:
        client.modify_global_cluster(
            GlobalClusterIdentifier=global_id,
            DeletionProtection=False,
        )
    except ClientError:
        pass
    try:
        client.delete_global_cluster(GlobalClusterIdentifier=global_id)
    except ClientError:
        pass


def test_rds_clusters_are_region_scoped():
    east = _regional_rds("us-east-1")
    west = _regional_rds("us-west-2")
    east_only = f"rds-east-only-{uuid.uuid4().hex[:8]}"
    shared = f"rds-shared-{uuid.uuid4().hex[:8]}"

    try:
        east.create_db_cluster(
            DBClusterIdentifier=east_only,
            Engine="aurora-mysql",
            MasterUsername="admin",
            MasterUserPassword="password123",
        )
        with pytest.raises(ClientError) as exc:
            west.describe_db_clusters(DBClusterIdentifier=east_only)
        assert exc.value.response["Error"]["Code"] == "DBClusterNotFoundFault"

        east.create_db_cluster(
            DBClusterIdentifier=shared,
            Engine="aurora-mysql",
            MasterUsername="admin",
            MasterUserPassword="password123",
            DatabaseName="eastdb",
        )
        west.create_db_cluster(
            DBClusterIdentifier=shared,
            Engine="aurora-mysql",
            MasterUsername="admin",
            MasterUserPassword="password123",
            DatabaseName="westdb",
        )

        east_cluster = east.describe_db_clusters(DBClusterIdentifier=shared)["DBClusters"][0]
        west_cluster = west.describe_db_clusters(DBClusterIdentifier=shared)["DBClusters"][0]
        assert east_cluster["DBClusterArn"] != west_cluster["DBClusterArn"]
        assert ":us-east-1:" in east_cluster["DBClusterArn"]
        assert ":us-west-2:" in west_cluster["DBClusterArn"]
        assert east_cluster["DatabaseName"] == "eastdb"
        assert west_cluster["DatabaseName"] == "westdb"
    finally:
        for client, cluster_id in (
            (east, east_only),
            (east, shared),
            (west, shared),
        ):
            _delete_cluster(client, cluster_id)


def test_rds_cluster_arn_lookup_rejects_foreign_account():
    account_a = _regional_rds("us-west-2", access_key_id="111111111111")
    account_b = _regional_rds("us-west-2", access_key_id="222222222222")
    cluster_id = f"rds-cross-account-{uuid.uuid4().hex[:8]}"

    try:
        cluster = account_a.create_db_cluster(
            DBClusterIdentifier=cluster_id,
            Engine="aurora-mysql",
            MasterUsername="admin",
            MasterUserPassword="password123",
        )["DBCluster"]

        same_account = account_a.describe_db_clusters(
            DBClusterIdentifier=cluster["DBClusterArn"],
        )["DBClusters"][0]
        assert same_account["DBClusterIdentifier"] == cluster_id

        with pytest.raises(ClientError) as exc:
            account_b.describe_db_clusters(DBClusterIdentifier=cluster["DBClusterArn"])
        assert exc.value.response["Error"]["Code"] == "DBClusterNotFoundFault"
    finally:
        _delete_cluster(account_a, cluster_id)


def test_rds_regional_cluster_apis_reject_foreign_region_arns():
    east = _regional_rds("us-east-1")
    west = _regional_rds("us-west-2")
    cluster_id = f"rds-foreign-region-{uuid.uuid4().hex[:8]}"

    try:
        cluster = west.create_db_cluster(
            DBClusterIdentifier=cluster_id,
            Engine="aurora-mysql",
            MasterUsername="admin",
            MasterUserPassword="password123",
        )["DBCluster"]
        cluster_arn = cluster["DBClusterArn"]

        same_region = west.describe_db_clusters(
            DBClusterIdentifier=cluster_arn,
        )["DBClusters"][0]
        assert same_region["DBClusterIdentifier"] == cluster_id

        with pytest.raises(ClientError) as exc:
            east.describe_db_clusters(DBClusterIdentifier=cluster_arn)
        assert exc.value.response["Error"]["Code"] == "InvalidParameterValue"

        with pytest.raises(ClientError) as exc:
            east.modify_db_cluster(
                DBClusterIdentifier=cluster_arn,
                BackupRetentionPeriod=1,
                ApplyImmediately=True,
            )
        assert exc.value.response["Error"]["Code"] == "InvalidParameterValue"

        with pytest.raises(ClientError) as exc:
            east.delete_db_cluster(DBClusterIdentifier=cluster_arn, SkipFinalSnapshot=True)
        assert exc.value.response["Error"]["Code"] == "InvalidParameterValue"

        with pytest.raises(ClientError) as exc:
            east.enable_http_endpoint(ResourceArn=cluster_arn)
        assert exc.value.response["Error"]["Code"] == "ResourceNotFoundFault"
    finally:
        _delete_cluster(west, cluster_id)


def test_rds_instances_are_region_scoped():
    east = _regional_rds("us-east-1")
    west = _regional_rds("us-west-2")
    shared = f"rds-inst-shared-{uuid.uuid4().hex[:8]}"

    try:
        east.create_db_instance(
            DBInstanceIdentifier=shared,
            DBInstanceClass="db.t3.micro",
            Engine="postgres",
            MasterUsername="admin",
            MasterUserPassword="pass",
            AllocatedStorage=10,
        )
        west.create_db_instance(
            DBInstanceIdentifier=shared,
            DBInstanceClass="db.t3.small",
            Engine="postgres",
            MasterUsername="admin",
            MasterUserPassword="pass",
            AllocatedStorage=20,
        )

        east_instance = east.describe_db_instances(DBInstanceIdentifier=shared)["DBInstances"][0]
        west_instance = west.describe_db_instances(DBInstanceIdentifier=shared)["DBInstances"][0]
        assert east_instance["DBInstanceArn"] != west_instance["DBInstanceArn"]
        assert ":us-east-1:" in east_instance["DBInstanceArn"]
        assert ":us-west-2:" in west_instance["DBInstanceArn"]
        assert east_instance["DBInstanceClass"] == "db.t3.micro"
        assert west_instance["DBInstanceClass"] == "db.t3.small"
    finally:
        for client in (east, west):
            try:
                client.delete_db_instance(DBInstanceIdentifier=shared, SkipFinalSnapshot=True)
            except ClientError:
                pass


def test_rds_regional_instance_apis_reject_foreign_region_arns():
    east = _regional_rds("us-east-1")
    west = _regional_rds("us-west-2")
    instance_id = f"rds-inst-arn-{uuid.uuid4().hex[:8]}"
    snapshot_id = f"rds-inst-arn-snap-{uuid.uuid4().hex[:8]}"

    try:
        instance = west.create_db_instance(
            DBInstanceIdentifier=instance_id,
            DBInstanceClass="db.t3.micro",
            Engine="postgres",
            MasterUsername="admin",
            MasterUserPassword="pass",
            AllocatedStorage=10,
        )["DBInstance"]
        instance_arn = instance["DBInstanceArn"]

        same_region = west.describe_db_instances(DBInstanceIdentifier=instance_arn)["DBInstances"][0]
        assert same_region["DBInstanceIdentifier"] == instance_id

        with pytest.raises(ClientError) as exc:
            east.describe_db_instances(DBInstanceIdentifier=instance_arn)
        assert exc.value.response["Error"]["Code"] == "InvalidParameterValue"

        with pytest.raises(ClientError) as exc:
            east.modify_db_instance(
                DBInstanceIdentifier=instance_arn,
                DBInstanceClass="db.t3.small",
                ApplyImmediately=True,
            )
        assert exc.value.response["Error"]["Code"] == "InvalidParameterValue"

        with pytest.raises(ClientError) as exc:
            east.create_db_snapshot(
                DBSnapshotIdentifier=snapshot_id,
                DBInstanceIdentifier=instance_arn,
            )
        assert exc.value.response["Error"]["Code"] == "InvalidParameterValue"

        with pytest.raises(ClientError) as exc:
            east.delete_db_instance(DBInstanceIdentifier=instance_arn, SkipFinalSnapshot=True)
        assert exc.value.response["Error"]["Code"] == "InvalidParameterValue"
    finally:
        try:
            west.delete_db_instance(DBInstanceIdentifier=instance_id, SkipFinalSnapshot=True)
        except ClientError:
            pass


def test_rds_tag_resource_arns_are_request_region_scoped():
    east = _regional_rds("us-east-1")
    west = _regional_rds("us-west-2")
    cluster_id = f"rds-tag-scope-{uuid.uuid4().hex[:8]}"

    try:
        cluster = west.create_db_cluster(
            DBClusterIdentifier=cluster_id,
            Engine="aurora-mysql",
            MasterUsername="admin",
            MasterUserPassword="password123",
        )["DBCluster"]
        cluster_arn = cluster["DBClusterArn"]
        bogus_account_arn = cluster_arn.replace(":000000000000:", ":111111111111:")

        west.add_tags_to_resource(
            ResourceName=cluster_arn,
            Tags=[{"Key": "scope", "Value": "west"}],
        )
        assert west.list_tags_for_resource(ResourceName=cluster_arn)["TagList"] == [
            {"Key": "scope", "Value": "west"},
        ]

        with pytest.raises(ClientError) as exc:
            east.add_tags_to_resource(
                ResourceName=cluster_arn,
                Tags=[{"Key": "scope", "Value": "east"}],
            )
        assert exc.value.response["Error"]["Code"] == "InvalidParameterValue"

        with pytest.raises(ClientError) as exc:
            east.list_tags_for_resource(ResourceName=cluster_arn)
        assert exc.value.response["Error"]["Code"] == "InvalidParameterValue"

        with pytest.raises(ClientError) as exc:
            west.add_tags_to_resource(
                ResourceName=bogus_account_arn,
                Tags=[{"Key": "scope", "Value": "bogus"}],
            )
        assert exc.value.response["Error"]["Code"] == "InvalidParameterValue"

        assert west.describe_db_clusters(DBClusterIdentifier=cluster_id)["DBClusters"][0]["TagList"] == [
            {"Key": "scope", "Value": "west"},
        ]
    finally:
        _delete_cluster(west, cluster_id)


def test_rds_cluster_snapshot_from_arn_stores_canonical_cluster_id():
    east = _regional_rds("us-east-1")
    suffix = uuid.uuid4().hex[:8]
    cluster_id = f"rds-snap-arn-{suffix}"
    snapshot_id = f"rds-snap-arn-{suffix}"

    try:
        cluster = east.create_db_cluster(
            DBClusterIdentifier=cluster_id,
            Engine="aurora-mysql",
            MasterUsername="admin",
            MasterUserPassword="password123",
        )["DBCluster"]
        east.create_db_cluster_snapshot(
            DBClusterSnapshotIdentifier=snapshot_id,
            DBClusterIdentifier=cluster["DBClusterArn"],
        )

        by_snapshot = east.describe_db_cluster_snapshots(
            DBClusterSnapshotIdentifier=snapshot_id,
        )["DBClusterSnapshots"][0]
        assert by_snapshot["DBClusterIdentifier"] == cluster_id

        by_cluster = east.describe_db_cluster_snapshots(
            DBClusterIdentifier=cluster_id,
        )["DBClusterSnapshots"]
        assert any(s["DBClusterSnapshotIdentifier"] == snapshot_id for s in by_cluster)
    finally:
        try:
            east.delete_db_cluster_snapshot(DBClusterSnapshotIdentifier=snapshot_id)
        except ClientError:
            pass
        _delete_cluster(east, cluster_id)


def test_describe_global_clusters_rejects_global_cluster_arns():
    account_a = _regional_rds("us-east-1", access_key_id="111111111111")
    account_b = _regional_rds("us-east-1", access_key_id="222222222222")
    global_id = f"global-cross-account-{uuid.uuid4().hex[:8]}"

    try:
        global_cluster = account_a.create_global_cluster(
            GlobalClusterIdentifier=global_id,
            Engine="aurora-mysql",
        )["GlobalCluster"]

        same_account = account_a.describe_global_clusters(
            GlobalClusterIdentifier=global_id,
        )["GlobalClusters"][0]
        assert same_account["GlobalClusterIdentifier"] == global_id

        with pytest.raises(ClientError) as exc:
            account_a.describe_global_clusters(
                GlobalClusterIdentifier=global_cluster["GlobalClusterArn"],
            )
        assert exc.value.response["Error"]["Code"] == "InvalidParameterValue"

        with pytest.raises(ClientError) as exc:
            account_b.describe_global_clusters(
                GlobalClusterIdentifier=global_cluster["GlobalClusterArn"],
            )
        assert exc.value.response["Error"]["Code"] == "InvalidParameterValue"
    finally:
        _delete_global_cluster(account_a, global_id)


def test_create_db_cluster_first_global_member_is_writer():
    east = _regional_rds("us-east-1")
    suffix = uuid.uuid4().hex[:8]
    global_id = f"global-empty-{suffix}"
    cluster_id = f"global-first-{suffix}"

    try:
        east.create_global_cluster(
            GlobalClusterIdentifier=global_id,
            Engine="aurora-mysql",
        )
        cluster = east.create_db_cluster(
            DBClusterIdentifier=cluster_id,
            Engine="aurora-mysql",
            GlobalClusterIdentifier=global_id,
            MasterUsername="admin",
            MasterUserPassword="password123",
        )["DBCluster"]

        global_cluster = east.describe_global_clusters(GlobalClusterIdentifier=global_id)["GlobalClusters"][0]
        members = {m["DBClusterArn"]: m for m in global_cluster["GlobalClusterMembers"]}
        assert members[cluster["DBClusterArn"]]["IsWriter"] is True
    finally:
        _remove_global_member(east, global_id, cluster_id)
        _delete_cluster(east, cluster_id)
        _delete_global_cluster(east, global_id)


def test_aurora_global_metadata_spans_regions():
    east = _regional_rds("us-east-1")
    west = _regional_rds("us-west-2")
    suffix = uuid.uuid4().hex[:8]
    primary_id = f"global-primary-{suffix}"
    secondary_id = f"global-secondary-{suffix}"
    global_id = f"global-metadata-{suffix}"

    try:
        primary = east.create_db_cluster(
            DBClusterIdentifier=primary_id,
            Engine="aurora-mysql",
            MasterUsername="admin",
            MasterUserPassword="password123",
        )["DBCluster"]
        east.create_global_cluster(
            GlobalClusterIdentifier=global_id,
            SourceDBClusterIdentifier=primary["DBClusterArn"],
            DeletionProtection=True,
        )

        primary_after_attach = east.describe_db_clusters(DBClusterIdentifier=primary_id)["DBClusters"][0]
        assert primary_after_attach["GlobalClusterIdentifier"] == global_id

        west.create_db_cluster(
            DBClusterIdentifier=secondary_id,
            Engine="aurora-mysql",
            GlobalClusterIdentifier=global_id,
            KmsKeyId="alias/aws/rds",
            MasterUsername="admin",
            MasterUserPassword="password123",
        )
        secondary = west.describe_db_clusters(DBClusterIdentifier=secondary_id)["DBClusters"][0]
        assert secondary["GlobalClusterIdentifier"] == global_id
        assert secondary["KmsKeyId"] == "alias/aws/rds"

        east_global = east.describe_global_clusters(GlobalClusterIdentifier=global_id)["GlobalClusters"][0]
        west_global = west.describe_global_clusters(GlobalClusterIdentifier=global_id)["GlobalClusters"][0]
        assert east_global == west_global
        assert east_global["DeletionProtection"] is True

        members = east_global["GlobalClusterMembers"]
        by_arn = {m["DBClusterArn"]: m for m in members}
        assert set(by_arn) == {primary["DBClusterArn"], secondary["DBClusterArn"]}
        assert by_arn[primary["DBClusterArn"]]["IsWriter"] is True
        assert by_arn[secondary["DBClusterArn"]]["IsWriter"] is False
        assert by_arn[secondary["DBClusterArn"]]["SynchronizationStatus"] == "connected"
        assert by_arn[secondary["DBClusterArn"]]["GlobalWriteForwardingStatus"] == "disabled"

        with pytest.raises(ClientError) as exc:
            west.delete_db_cluster(DBClusterIdentifier=secondary_id, SkipFinalSnapshot=True)
        assert exc.value.response["Error"]["Code"] == "InvalidDBClusterStateFault"

        east.modify_global_cluster(GlobalClusterIdentifier=global_id, DeletionProtection=False)
        east.modify_db_cluster(DBClusterIdentifier=primary_id, DeletionProtection=True)
        assert east.describe_db_clusters(DBClusterIdentifier=primary_id)["DBClusters"][0]["DeletionProtection"] is True
        east.modify_db_cluster(DBClusterIdentifier=primary_id, DeletionProtection=False)

        with pytest.raises(ClientError) as exc:
            east.delete_global_cluster(GlobalClusterIdentifier=global_id)
        assert exc.value.response["Error"]["Code"] == "InvalidGlobalClusterStateFault"

        with pytest.raises(ClientError) as exc:
            west.remove_from_global_cluster(
                GlobalClusterIdentifier=global_id,
                DbClusterIdentifier=primary["DBClusterArn"],
            )
        assert exc.value.response["Error"]["Code"] == "InvalidGlobalClusterStateFault"

        east.remove_from_global_cluster(
            GlobalClusterIdentifier=global_id,
            DbClusterIdentifier=secondary["DBClusterArn"],
        )
        secondary_after_detach = west.describe_db_clusters(DBClusterIdentifier=secondary_id)["DBClusters"][0]
        assert "GlobalClusterIdentifier" not in secondary_after_detach
        assert len(east.describe_global_clusters(GlobalClusterIdentifier=global_id)["GlobalClusters"][0]["GlobalClusterMembers"]) == 1
        west.delete_db_cluster(DBClusterIdentifier=secondary_id, SkipFinalSnapshot=True)

        east.remove_from_global_cluster(
            GlobalClusterIdentifier=global_id,
            DbClusterIdentifier=primary["DBClusterArn"],
        )
        assert east.describe_global_clusters(GlobalClusterIdentifier=global_id)["GlobalClusters"][0]["GlobalClusterMembers"] == []
        east.delete_global_cluster(GlobalClusterIdentifier=global_id)
        east.delete_db_cluster(DBClusterIdentifier=primary_id, SkipFinalSnapshot=True)
    finally:
        _remove_global_member(west, global_id, secondary_id)
        _remove_global_member(east, global_id, primary_id)
        _delete_global_cluster(east, global_id)
        _delete_cluster(west, secondary_id)
        _delete_cluster(east, primary_id)


def test_aurora_engine_versions_advertise_global_database_support():
    rds = _regional_rds("us-east-1")

    resp = rds.describe_db_engine_versions(Engine="aurora-mysql")
    assert resp["DBEngineVersions"]
    assert all(v["SupportsGlobalDatabases"] is True for v in resp["DBEngineVersions"])
