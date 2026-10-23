/*
 * NiceVPN Community Set Filter for AG Grid.
 *
 * Mirrors the UX of AG Grid Enterprise Set Filter while staying on the
 * Community build: mini search, select all, unique values, multi-select.
 * Visual-only component; filtering remains entirely client-side in AG Grid.
 */
(() => {
  class NiceVpnSetFilter {
    init(params) {
      this.params = params;
      this.allValues = [];
      this.selectedValues = new Set();
      this.searchText = '';

      this.gui = document.createElement('div');
      this.gui.className = 'vpn-grid-set-filter';

      this.searchWrap = document.createElement('label');
      this.searchWrap.className = 'vpn-grid-set-filter__search';

      const searchIcon = document.createElement('span');
      searchIcon.className = 'material-icons vpn-grid-set-filter__search-icon';
      searchIcon.textContent = 'search';

      this.searchInput = document.createElement('input');
      this.searchInput.type = 'search';
      this.searchInput.autocomplete = 'off';
      this.searchInput.spellcheck = false;
      this.searchInput.placeholder = String(params.searchPlaceholder || '搜索...');

      this.searchWrap.append(searchIcon, this.searchInput);

      this.list = document.createElement('div');
      this.list.className = 'vpn-grid-set-filter__list';

      this.gui.append(this.searchWrap, this.list);

      this.searchInput.addEventListener('input', () => {
        this.searchText = this.searchInput.value.trim().toLocaleLowerCase();
        this.renderList();
      });

      this.refreshValues(false);
    }

    getGui() {
      return this.gui;
    }

    afterGuiAttached() {
      window.setTimeout(() => this.searchInput?.focus(), 0);
    }

    isFilterActive() {
      return this.selectedValues.size !== this.allValues.length;
    }

    filterValue(node) {
      const field = String(this.params.filterValueField || '').trim();
      if (field) {
        return this.normalize(node?.data?.[field]);
      }
      return this.normalize(this.params.getValue(node));
    }

    doesFilterPass(params) {
      if (!this.isFilterActive()) {
        return true;
      }
      const value = this.filterValue(params.node);
      return this.selectedValues.has(value);
    }

    getModel() {
      if (!this.isFilterActive()) {
        return null;
      }
      return {
        filterType: 'niceVpnSet',
        values: this.allValues.filter(value => this.selectedValues.has(value)),
      };
    }

    setModel(model) {
      if (!model || !Array.isArray(model.values)) {
        this.selectedValues = new Set(this.allValues);
      } else {
        this.selectedValues = new Set(model.values.map(value => this.normalize(value)));
      }
      this.renderList();
    }

    getModelAsString() {
      if (!this.isFilterActive()) {
        return '';
      }
      return Array.from(this.selectedValues).join(', ');
    }

    onNewRowsLoaded() {
      this.refreshValues(true);
    }

    onAnyFilterChanged() {
      this.refreshValues(true);
    }

    normalize(value) {
      if (value === null || value === undefined || value === '') {
        return String(this.params.blankLabel || '(空白)');
      }
      return String(value);
    }

    collectValues() {
      const values = new Set();
      this.params.api.forEachLeafNode(node => {
        if (
          typeof this.params.doesRowPassOtherFilter === 'function' &&
          !this.params.doesRowPassOtherFilter(node)
        ) {
          return;
        }
        values.add(this.filterValue(node));
      });

      const preferred = Array.isArray(this.params.valueOrder)
        ? this.params.valueOrder.map(value => this.normalize(value))
        : [];
      const preferredIndex = new Map(preferred.map((value, index) => [value, index]));

      return Array.from(values).sort((left, right) => {
        const leftIndex = preferredIndex.has(left) ? preferredIndex.get(left) : Number.MAX_SAFE_INTEGER;
        const rightIndex = preferredIndex.has(right) ? preferredIndex.get(right) : Number.MAX_SAFE_INTEGER;
        if (leftIndex !== rightIndex) {
          return leftIndex - rightIndex;
        }
        return left.localeCompare(right, 'zh-CN', { numeric: true, sensitivity: 'base' });
      });
    }

    refreshValues(preserveSelection) {
      const previousValues = new Set(this.allValues);
      const previousSelection = new Set(this.selectedValues);
      const wasInactive = previousValues.size === previousSelection.size;

      this.allValues = this.collectValues();

      if (!preserveSelection || wasInactive) {
        this.selectedValues = new Set(this.allValues);
      } else {
        this.selectedValues = new Set(
          this.allValues.filter(value => previousSelection.has(value)),
        );
      }

      this.renderList();
    }

    visibleValues() {
      if (!this.searchText) {
        return this.allValues;
      }
      return this.allValues.filter(value =>
        value.toLocaleLowerCase().includes(this.searchText),
      );
    }

    notifyFilterChanged() {
      this.params.filterModifiedCallback?.();
      this.params.filterChangedCallback?.();
    }

    createCheckbox(checked, indeterminate = false) {
      const checkbox = document.createElement('input');
      checkbox.type = 'checkbox';
      checkbox.className = 'vpn-grid-set-filter__checkbox';
      checkbox.checked = checked;
      checkbox.indeterminate = indeterminate;
      return checkbox;
    }

    renderList() {
      if (!this.list) {
        return;
      }

      this.list.replaceChildren();
      const visible = this.visibleValues();
      const selectedVisibleCount = visible.filter(value => this.selectedValues.has(value)).length;
      const allVisibleSelected = visible.length > 0 && selectedVisibleCount === visible.length;
      const someVisibleSelected = selectedVisibleCount > 0 && !allVisibleSelected;

      const selectAllRow = document.createElement('label');
      selectAllRow.className = 'vpn-grid-set-filter__option vpn-grid-set-filter__option--all';

      const selectAll = this.createCheckbox(allVisibleSelected, someVisibleSelected);
      const selectAllText = document.createElement('span');
      selectAllText.textContent = String(this.params.selectAllLabel || '(全选)');

      selectAllRow.append(selectAll, selectAllText);
      selectAll.addEventListener('change', () => {
        if (selectAll.checked) {
          visible.forEach(value => this.selectedValues.add(value));
        } else {
          visible.forEach(value => this.selectedValues.delete(value));
        }
        this.renderList();
        this.notifyFilterChanged();
      });
      this.list.append(selectAllRow);

      if (visible.length === 0) {
        const empty = document.createElement('div');
        empty.className = 'vpn-grid-set-filter__empty';
        empty.textContent = String(this.params.noMatchesLabel || '没有匹配项');
        this.list.append(empty);
        return;
      }

      visible.forEach(value => {
        const row = document.createElement('label');
        row.className = 'vpn-grid-set-filter__option';

        const checkbox = this.createCheckbox(this.selectedValues.has(value));
        const text = document.createElement('span');
        text.className = 'vpn-grid-set-filter__option-text';
        text.textContent = value;

        row.append(checkbox, text);
        checkbox.addEventListener('change', () => {
          if (checkbox.checked) {
            this.selectedValues.add(value);
          } else {
            this.selectedValues.delete(value);
          }
          this.renderList();
          this.notifyFilterChanged();
        });

        this.list.append(row);
      });
    }
  }

  window.NiceVpnSetFilter = NiceVpnSetFilter;
})();
