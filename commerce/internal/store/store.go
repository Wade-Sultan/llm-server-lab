// Package store owns commerce's Postgres access: connection setup via the
// Cloud SQL Go connector (IAM DB auth, no password) and the query functions
// backing the users and listings features.
//
// Same shared Postgres instance/schema as the Python "builder" service and
// admin's Prisma client — schema changes are Alembic's responsibility
// (backend/app/alembic/); this package just reads/writes the tables it needs.
package store

import (
	"context"
	"database/sql"
	"errors"
	"fmt"
	"time"

	"cloud.google.com/go/cloudsqlconn"
	"cloud.google.com/go/cloudsqlconn/postgres/pgxv5"
	"github.com/google/uuid"
	"github.com/jackc/pgx/v5/pgconn"
	_ "github.com/jackc/pgx/v5/stdlib" // "pgx" database/sql driver for the dev DATABASE_URL path

	"github.com/palladium/commerce/internal/config"
)

// ErrNotFound is returned by single-row lookups when no row matches.
var ErrNotFound = errors.New("not found")

// ErrConflict is returned when a write violates a foreign-key constraint,
// e.g. deleting a user who still owns a PCBuild.
var ErrConflict = errors.New("conflict")

const driverName = "cloudsql-postgres-commerce"

type Store struct {
	db      *sql.DB
	cleanup func() error
}

// New opens a connection pool to the shared Postgres instance. In prod it uses
// the Cloud SQL connector with IAM database authentication (cfg.DBIAMUser, no
// password); when cfg.DatabaseURL is set (local dev via the cloud-sql-proxy) it
// connects with the plain pgx driver over that DSN instead.
func New(ctx context.Context, cfg *config.Config) (*Store, error) {
	var (
		db      *sql.DB
		cleanup func() error
		err     error
	)
	if cfg.DatabaseURL != "" {
		db, cleanup, err = openDirect(cfg.DatabaseURL)
	} else {
		db, cleanup, err = openConnector(cfg)
	}
	if err != nil {
		return nil, err
	}

	db.SetMaxOpenConns(5)
	db.SetMaxIdleConns(5)
	db.SetConnMaxLifetime(30 * time.Minute)

	pingCtx, cancel := context.WithTimeout(ctx, 10*time.Second)
	defer cancel()
	if err := db.PingContext(pingCtx); err != nil {
		_ = db.Close()
		_ = cleanup()
		return nil, fmt.Errorf("ping db: %w", err)
	}

	return &Store{db: db, cleanup: cleanup}, nil
}

// openDirect connects with the plain pgx database/sql driver over a standard
// Postgres DSN — the local-dev path (cloud-sql-proxy on localhost:5433). It
// registers no connector driver, so cleanup is a no-op.
func openDirect(dsn string) (*sql.DB, func() error, error) {
	db, err := sql.Open("pgx", dsn)
	if err != nil {
		return nil, nil, fmt.Errorf("open db: %w", err)
	}
	return db, func() error { return nil }, nil
}

// openConnector connects via the Cloud SQL Go connector using IAM database
// authentication (cfg.DBIAMUser) rather than a password. The returned cleanup
// deregisters the connector driver and must be called on shutdown.
func openConnector(cfg *config.Config) (*sql.DB, func() error, error) {
	opts := []cloudsqlconn.Option{cloudsqlconn.WithIAMAuthN()}
	if cfg.DBUsePrivateIP {
		opts = append(opts, cloudsqlconn.WithDefaultDialOptions(cloudsqlconn.WithPrivateIP()))
	}

	cleanup, err := pgxv5.RegisterDriver(driverName, opts...)
	if err != nil {
		return nil, nil, fmt.Errorf("register cloud sql driver: %w", err)
	}

	dsn := fmt.Sprintf("host=%s user=%s dbname=%s sslmode=disable",
		cfg.InstanceConnectionName, cfg.DBIAMUser, cfg.DBName)

	db, err := sql.Open(driverName, dsn)
	if err != nil {
		_ = cleanup()
		return nil, nil, fmt.Errorf("open db: %w", err)
	}
	return db, cleanup, nil
}

// Ping verifies the pool can still reach the database. Backs the readiness
// probe, which is deliberately separate from liveness: a DB blip should pull
// this instance out of the Service's endpoints, not restart it.
func (s *Store) Ping(ctx context.Context) error {
	return s.db.PingContext(ctx)
}

func (s *Store) Close() error {
	closeErr := s.db.Close()
	cleanupErr := s.cleanup()
	if closeErr != nil {
		return closeErr
	}
	return cleanupErr
}

// isForeignKeyViolation reports whether err is a Postgres FK-violation
// (SQLSTATE 23503), e.g. deleting a user who still owns a PCBuild.
func isForeignKeyViolation(err error) bool {
	var pgErr *pgconn.PgError
	return errors.As(err, &pgErr) && pgErr.Code == "23503"
}

// --- Users -------------------------------------------------------------------

type User struct {
	ID          string
	FirebaseUID *string
	Email       string
	Username    *string
	IsActive    bool
	IsSuperuser bool
	CreatedAt   time.Time
	UpdatedAt   time.Time
}

const userColumns = `id, firebase_uid, email, username, is_active, is_superuser, created_at, updated_at`

func scanUser(row interface{ Scan(dest ...any) error }) (*User, error) {
	var u User
	if err := row.Scan(&u.ID, &u.FirebaseUID, &u.Email, &u.Username, &u.IsActive, &u.IsSuperuser, &u.CreatedAt, &u.UpdatedAt); err != nil {
		return nil, err
	}
	return &u, nil
}

func (s *Store) GetUserByFirebaseUID(ctx context.Context, firebaseUID string) (*User, error) {
	row := s.db.QueryRowContext(ctx,
		`SELECT `+userColumns+` FROM users WHERE firebase_uid = $1`, firebaseUID)
	u, err := scanUser(row)
	if errors.Is(err, sql.ErrNoRows) {
		return nil, ErrNotFound
	}
	if err != nil {
		return nil, err
	}
	return u, nil
}

// GetUserByID looks a user up by primary key. This is the internal path: the
// builder's pricing ETL knows which customer a price subscription belongs to
// but deliberately never carries their address, so resolving id -> email
// happens here, against the row that is the source of truth for it.
func (s *Store) GetUserByID(ctx context.Context, id string) (*User, error) {
	// Parsed rather than passed straight through: users.id is uuid, and a
	// malformed value would otherwise reach Postgres as a type error (a 500)
	// instead of the not-found this is.
	if _, err := uuid.Parse(id); err != nil {
		return nil, ErrNotFound
	}
	row := s.db.QueryRowContext(ctx,
		`SELECT `+userColumns+` FROM users WHERE id = $1`, id)
	u, err := scanUser(row)
	if errors.Is(err, sql.ErrNoRows) {
		return nil, ErrNotFound
	}
	if err != nil {
		return nil, err
	}
	return u, nil
}

func (s *Store) GetUserByEmail(ctx context.Context, email string) (*User, error) {
	row := s.db.QueryRowContext(ctx,
		`SELECT `+userColumns+` FROM users WHERE email = $1`, email)
	u, err := scanUser(row)
	if errors.Is(err, sql.ErrNoRows) {
		return nil, ErrNotFound
	}
	if err != nil {
		return nil, err
	}
	return u, nil
}

// CreateUser inserts a Firebase-authenticated user. hashed_password has no
// server default and no real meaning here — "!firebase_oauth" is the
// established sentinel (see backend/app/api/routes/chat.py::_save_turn) for
// accounts that only ever authenticate via Firebase.
func (s *Store) CreateUser(ctx context.Context, firebaseUID, email string) (*User, error) {
	id := uuid.NewString()
	row := s.db.QueryRowContext(ctx,
		`INSERT INTO users (id, firebase_uid, email, hashed_password, is_active, is_superuser)
		 VALUES ($1, $2, $3, '!firebase_oauth', true, false)
		 RETURNING `+userColumns,
		id, firebaseUID, email)
	return scanUser(row)
}

// LinkFirebaseUID attaches a Firebase UID to a user row that was matched by
// email but doesn't have one yet (e.g. auto-provisioned by builder's chat
// pipeline before commerce ever saw this user).
func (s *Store) LinkFirebaseUID(ctx context.Context, userID, firebaseUID string) (*User, error) {
	row := s.db.QueryRowContext(ctx,
		`UPDATE users SET firebase_uid = $1 WHERE id = $2 AND firebase_uid IS NULL
		 RETURNING `+userColumns,
		firebaseUID, userID)
	return scanUser(row)
}

// DeleteUserByFirebaseUID deletes a user row. Conversations cascade at the DB
// level (ondelete="CASCADE"); PCBuild ownership does not, so this returns
// ErrConflict if the user still owns builds rather than surfacing a raw
// Postgres error.
func (s *Store) DeleteUserByFirebaseUID(ctx context.Context, firebaseUID string) error {
	res, err := s.db.ExecContext(ctx, `DELETE FROM users WHERE firebase_uid = $1`, firebaseUID)
	if err != nil {
		if isForeignKeyViolation(err) {
			return ErrConflict
		}
		return err
	}
	n, err := res.RowsAffected()
	if err != nil {
		return err
	}
	if n == 0 {
		return ErrNotFound
	}
	return nil
}

// --- Listings ----------------------------------------------------------------

// Listing mirrors the columns returned by the Python API's ListingPublic /
// AmazonListingPublic schemas (backend/app/schemas/listing.py). Only the
// "amazon" listing_type is populated in practice today, so Amazon-specific
// columns are plain nullable fields here rather than a separate type.
type Listing struct {
	ID string
	// Nil when the listing targets a group rather than one part — see
	// partOrGroupMatchFmt and the ck_listings_one_target constraint.
	PartID             *string
	ListingType        string
	Marketplace        string
	URL                *string
	PriceAmount        *int64
	Currency           *string
	ImageURL           *string
	IsActive           bool
	FetchedAt          *time.Time
	PriceLastUpdatedAt *time.Time
	CreatedAt          time.Time
	UpdatedAt          time.Time

	ASIN         *string
	Brand        *string
	IsPrime      *bool
	SellerName   *string
	AffiliateTag *string
}

const listingColumns = `
	l.id, l.part_id, l.listing_type, l.marketplace, l.url, l.price_amount,
	l.currency, l.image_url, l.is_active, l.fetched_at, l.price_last_updated_at,
	l.created_at, l.updated_at,
	a.asin, a.brand, a.is_prime, a.seller_name, a.affiliate_tag`

const listingFromJoin = `FROM listings l LEFT JOIN amazon_listings a ON a.id = l.id`

// partOrGroupMatchFmt selects the listings that apply to one part: its own,
// plus any attached to a group it belongs to. Each subselect yields NULL for a
// part of another type, and NULL equals nothing, so the unrelated branches are
// simply false without needing a part_type check here.
//
// %[1]d is the placeholder index for the part id, which it uses five times.
const partOrGroupMatchFmt = `(
	    l.part_id          = $%[1]d
	 OR l.gpu_chipset_id   = (SELECT gpu_chipset_id   FROM gpus           WHERE id = $%[1]d)
	 OR l.psu_group_id     = (SELECT psu_group_id     FROM psus           WHERE id = $%[1]d)
	 OR l.ram_group_id     = (SELECT ram_group_id     FROM ram_kits       WHERE id = $%[1]d)
	 OR l.storage_group_id = (SELECT storage_group_id FROM storage_drives WHERE id = $%[1]d)
	)`

// A part's own listing outranks its group's, so a board with a specific link
// keeps it while every other board of the chipset falls back to the generic
// one. Callers take the first row per marketplace, which makes this ordering
// the override rule rather than a cosmetic sort.
const specificFirstOrder = ` ORDER BY (l.part_id IS NULL), l.created_at`

func scanListing(row interface{ Scan(dest ...any) error }) (*Listing, error) {
	var l Listing
	if err := row.Scan(
		&l.ID, &l.PartID, &l.ListingType, &l.Marketplace, &l.URL, &l.PriceAmount,
		&l.Currency, &l.ImageURL, &l.IsActive, &l.FetchedAt, &l.PriceLastUpdatedAt,
		&l.CreatedAt, &l.UpdatedAt,
		&l.ASIN, &l.Brand, &l.IsPrime, &l.SellerName, &l.AffiliateTag,
	); err != nil {
		return nil, err
	}
	return &l, nil
}

type ListingFilter struct {
	PartID      *string
	Marketplace *string
	Skip        int
	Limit       int
}

func (s *Store) ListListings(ctx context.Context, f ListingFilter) ([]*Listing, error) {
	query := `SELECT ` + listingColumns + ` ` + listingFromJoin + ` WHERE l.is_active = true`
	args := []any{}
	if f.PartID != nil {
		args = append(args, *f.PartID)
		query += " AND " + fmt.Sprintf(partOrGroupMatchFmt, len(args))
	}
	if f.Marketplace != nil {
		args = append(args, *f.Marketplace)
		query += fmt.Sprintf(" AND l.marketplace = $%d", len(args))
	}
	query += specificFirstOrder
	args = append(args, f.Limit)
	query += fmt.Sprintf(" LIMIT $%d", len(args))
	args = append(args, f.Skip)
	query += fmt.Sprintf(" OFFSET $%d", len(args))

	rows, err := s.db.QueryContext(ctx, query, args...)
	if err != nil {
		return nil, err
	}
	defer rows.Close()

	var listings []*Listing
	for rows.Next() {
		l, err := scanListing(rows)
		if err != nil {
			return nil, err
		}
		listings = append(listings, l)
	}
	return listings, rows.Err()
}

func (s *Store) CountListings(ctx context.Context, f ListingFilter) (int, error) {
	query := `SELECT count(*) FROM listings l WHERE l.is_active = true`
	args := []any{}
	if f.PartID != nil {
		args = append(args, *f.PartID)
		query += " AND " + fmt.Sprintf(partOrGroupMatchFmt, len(args))
	}
	if f.Marketplace != nil {
		args = append(args, *f.Marketplace)
		query += fmt.Sprintf(" AND l.marketplace = $%d", len(args))
	}
	var count int
	err := s.db.QueryRowContext(ctx, query, args...).Scan(&count)
	return count, err
}

func (s *Store) GetListingByID(ctx context.Context, id string) (*Listing, error) {
	row := s.db.QueryRowContext(ctx,
		`SELECT `+listingColumns+` `+listingFromJoin+` WHERE l.id = $1`, id)
	l, err := scanListing(row)
	if errors.Is(err, sql.ErrNoRows) {
		return nil, ErrNotFound
	}
	if err != nil {
		return nil, err
	}
	return l, nil
}

// PartExists checks pc_parts, matching the 404 "PCPart not found" behavior of
// the Python route's read_listings_by_part.
func (s *Store) PartExists(ctx context.Context, partID string) (bool, error) {
	var exists bool
	err := s.db.QueryRowContext(ctx, `SELECT EXISTS(SELECT 1 FROM pc_parts WHERE id = $1)`, partID).Scan(&exists)
	return exists, err
}

func (s *Store) GetListingsByPartID(ctx context.Context, partID string) ([]*Listing, error) {
	rows, err := s.db.QueryContext(ctx,
		`SELECT `+listingColumns+` `+listingFromJoin+
			` WHERE l.is_active = true AND `+fmt.Sprintf(partOrGroupMatchFmt, 1)+
			specificFirstOrder, partID)
	if err != nil {
		return nil, err
	}
	defer rows.Close()

	var listings []*Listing
	for rows.Next() {
		l, err := scanListing(rows)
		if err != nil {
			return nil, err
		}
		listings = append(listings, l)
	}
	return listings, rows.Err()
}

// CreateListingInput mirrors schemas/listing.py's AmazonListingCreate — the
// only listing_type actually created in practice today.
type CreateListingInput struct {
	PartID       string
	Marketplace  string
	URL          *string
	PriceAmount  *int64
	Currency     *string
	ImageURL     *string
	ASIN         string
	Brand        *string
	IsPrime      *bool
	SellerName   *string
	AffiliateTag *string
}

func (s *Store) CreateListing(ctx context.Context, in CreateListingInput) (*Listing, error) {
	tx, err := s.db.BeginTx(ctx, nil)
	if err != nil {
		return nil, err
	}
	defer tx.Rollback()

	id := uuid.NewString()
	currency := "USD"
	if in.Currency != nil {
		currency = *in.Currency
	}
	if _, err := tx.ExecContext(ctx,
		`INSERT INTO listings (id, part_id, listing_type, marketplace, url, price_amount, currency, image_url, is_active)
		 VALUES ($1, $2, 'amazon', $3, $4, $5, $6, $7, true)`,
		id, in.PartID, in.Marketplace, in.URL, in.PriceAmount, currency, in.ImageURL); err != nil {
		if isForeignKeyViolation(err) {
			return nil, fmt.Errorf("part_id %q does not exist: %w", in.PartID, ErrConflict)
		}
		return nil, err
	}
	if _, err := tx.ExecContext(ctx,
		`INSERT INTO amazon_listings (id, asin, brand, is_prime, seller_name, affiliate_tag)
		 VALUES ($1, $2, $3, $4, $5, $6)`,
		id, in.ASIN, in.Brand, in.IsPrime, in.SellerName, in.AffiliateTag); err != nil {
		return nil, err
	}
	if err := tx.Commit(); err != nil {
		return nil, err
	}

	return s.GetListingByID(ctx, id)
}

// UpdateListingInput mirrors schemas/listing.py's ListingUpdate — all fields
// optional, only non-nil ones are applied.
type UpdateListingInput struct {
	URL                *string
	PriceAmount        *int64
	Currency           *string
	ImageURL           *string
	IsActive           *bool
	FetchedAt          *time.Time
	PriceLastUpdatedAt *time.Time
}

func (s *Store) UpdateListing(ctx context.Context, id string, in UpdateListingInput) (*Listing, error) {
	set := []string{}
	args := []any{}
	add := func(col string, val any) {
		args = append(args, val)
		set = append(set, fmt.Sprintf("%s = $%d", col, len(args)))
	}
	if in.URL != nil {
		add("url", *in.URL)
	}
	if in.PriceAmount != nil {
		add("price_amount", *in.PriceAmount)
	}
	if in.Currency != nil {
		add("currency", *in.Currency)
	}
	if in.ImageURL != nil {
		add("image_url", *in.ImageURL)
	}
	if in.IsActive != nil {
		add("is_active", *in.IsActive)
	}
	if in.FetchedAt != nil {
		add("fetched_at", *in.FetchedAt)
	}
	if in.PriceLastUpdatedAt != nil {
		add("price_last_updated_at", *in.PriceLastUpdatedAt)
	}
	if len(set) == 0 {
		return s.GetListingByID(ctx, id)
	}

	args = append(args, id)
	query := fmt.Sprintf(`UPDATE listings SET %s WHERE id = $%d`, joinComma(set), len(args))
	res, err := s.db.ExecContext(ctx, query, args...)
	if err != nil {
		return nil, err
	}
	n, err := res.RowsAffected()
	if err != nil {
		return nil, err
	}
	if n == 0 {
		return nil, ErrNotFound
	}
	return s.GetListingByID(ctx, id)
}

func (s *Store) DeleteListing(ctx context.Context, id string) error {
	res, err := s.db.ExecContext(ctx, `DELETE FROM listings WHERE id = $1`, id)
	if err != nil {
		return err
	}
	n, err := res.RowsAffected()
	if err != nil {
		return err
	}
	if n == 0 {
		return ErrNotFound
	}
	return nil
}

func joinComma(parts []string) string {
	out := ""
	for i, p := range parts {
		if i > 0 {
			out += ", "
		}
		out += p
	}
	return out
}
