package cloudapimigrations

import (
	"embed"
	"io/fs"
	"sort"
)

//go:embed postgres/*.sql
var postgresFS embed.FS

type Migration struct {
	Name string
	SQL  string
}

func PostgresMigrations() ([]Migration, error) {
	paths, err := fs.Glob(postgresFS, "postgres/*.sql")
	if err != nil {
		return nil, err
	}
	sort.Strings(paths)

	migrations := make([]Migration, 0, len(paths))
	for _, path := range paths {
		sql, err := postgresFS.ReadFile(path)
		if err != nil {
			return nil, err
		}
		migrations = append(migrations, Migration{
			Name: path,
			SQL:  string(sql),
		})
	}
	return migrations, nil
}
