import uuid

import pytest
from botocore.exceptions import ClientError
from conftest import make_client


def _regional_rds(region):
    return make_client("rds", region_name=region)


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
            try:
                client.delete_db_cluster(DBClusterIdentifier=cluster_id, SkipFinalSnapshot=True)
            except ClientError:
                pass


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
