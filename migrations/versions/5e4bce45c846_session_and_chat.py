"""session‑and‑chat

Revision ID: 5e4bce45c846
Revises: 5cf9995c8bc5
Create Date: 2025-04-11 12:31:50.733152

"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision = '5e4bce45c846'
down_revision = '5cf9995c8bc5'
branch_labels = None
depends_on = None


def upgrade():
    # ### commands auto generated by Alembic - please adjust! ###
    op.drop_table('route_comments')
    op.drop_table('route_points')
    op.drop_table('routes')
    # ### end Alembic commands ###


def downgrade():
    # ### commands auto generated by Alembic - please adjust! ###
    op.create_table('routes',
    sa.Column('id', sa.VARCHAR(length=36), autoincrement=False, nullable=False),
    sa.Column('name', sa.VARCHAR(length=120), autoincrement=False, nullable=True),
    sa.Column('username', sa.VARCHAR(length=80), autoincrement=False, nullable=True),
    sa.Column('distance', sa.DOUBLE_PRECISION(precision=53), autoincrement=False, nullable=True),
    sa.Column('created_at', postgresql.TIMESTAMP(), autoincrement=False, nullable=True),
    sa.ForeignKeyConstraint(['username'], ['users.username'], name='routes_username_fkey'),
    sa.PrimaryKeyConstraint('id', name='routes_pkey'),
    postgresql_ignore_search_path=False
    )
    op.create_table('route_points',
    sa.Column('id', sa.INTEGER(), autoincrement=True, nullable=False),
    sa.Column('route_id', sa.VARCHAR(length=36), autoincrement=False, nullable=True),
    sa.Column('lat', sa.DOUBLE_PRECISION(precision=53), autoincrement=False, nullable=True),
    sa.Column('lon', sa.DOUBLE_PRECISION(precision=53), autoincrement=False, nullable=True),
    sa.ForeignKeyConstraint(['route_id'], ['routes.id'], name='route_points_route_id_fkey'),
    sa.PrimaryKeyConstraint('id', name='route_points_pkey')
    )
    op.create_table('route_comments',
    sa.Column('id', sa.INTEGER(), autoincrement=True, nullable=False),
    sa.Column('route_id', sa.VARCHAR(length=36), autoincrement=False, nullable=True),
    sa.Column('lat', sa.DOUBLE_PRECISION(precision=53), autoincrement=False, nullable=True),
    sa.Column('lon', sa.DOUBLE_PRECISION(precision=53), autoincrement=False, nullable=True),
    sa.Column('text', sa.TEXT(), autoincrement=False, nullable=True),
    sa.Column('time', sa.VARCHAR(length=50), autoincrement=False, nullable=True),
    sa.Column('photo', sa.VARCHAR(length=200), autoincrement=False, nullable=True),
    sa.ForeignKeyConstraint(['route_id'], ['routes.id'], name='route_comments_route_id_fkey'),
    sa.PrimaryKeyConstraint('id', name='route_comments_pkey')
    )
    # ### end Alembic commands ###
