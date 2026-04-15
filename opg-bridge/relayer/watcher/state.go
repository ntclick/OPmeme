package watcher

import (
	"encoding/json"
	"os"
	"path/filepath"
	"sync"
)

// State persists last processed block per chain to disk so the relayer can resume.
type State struct {
	path string
	mu   sync.Mutex
	data map[string]uint64
}

func LoadState(dir string) (*State, error) {
	if err := os.MkdirAll(dir, 0o755); err != nil {
		return nil, err
	}
	path := filepath.Join(dir, "state.json")
	s := &State{path: path, data: map[string]uint64{}}
	b, err := os.ReadFile(path)
	if err != nil {
		if os.IsNotExist(err) {
			return s, nil
		}
		return nil, err
	}
	_ = json.Unmarshal(b, &s.data)
	return s, nil
}

func (s *State) Get(key string) uint64 {
	s.mu.Lock()
	defer s.mu.Unlock()
	return s.data[key]
}

func (s *State) Set(key string, block uint64) error {
	s.mu.Lock()
	defer s.mu.Unlock()
	s.data[key] = block
	b, err := json.MarshalIndent(s.data, "", "  ")
	if err != nil {
		return err
	}
	return os.WriteFile(s.path, b, 0o644)
}
