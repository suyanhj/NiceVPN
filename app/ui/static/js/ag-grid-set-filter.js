/*
 * NiceVPN Community Set Filter for AG Grid.
 *
 * Mirrors the UX of AG Grid Enterprise Set Filter while staying on the
 * Community build: mini search, select all, unique values, multi-select.
 * Can use server-provided filterParams.values so paged grids still expose the full value set.
 */
(() => {
  class NiceVpnSetFilter {
    init(params) {
      this.params = params;
      this.allValues = [];
      this.selectedValues = new Set();
      this.draftSelectedValues = new Set();
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

      this.actions = document.createElement('div');
      this.actions.className = 'vpn-grid-filter-actions';

      this.resetButton = document.createElement('button');
      this.resetButton.type = 'button';
      this.resetButton.className = 'vpn-grid-filter-actions__button';
      this.resetButton.textContent = '清除';

      this.applyButton = document.createElement('button');
      this.applyButton.type = 'button';
      this.applyButton.className = 'vpn-grid-filter-actions__button vpn-grid-filter-actions__button--primary';
      this.applyButton.textContent = '应用';

      this.actions.append(this.resetButton, this.applyButton);
      this.gui.append(this.searchWrap, this.list, this.actions);

      this.resetButton.addEventListener('click', () => {
        this.draftSelectedValues = new Set(this.allValues);
        this.renderList();
        this.notifyFilterModified();
      });
      this.applyButton.addEventListener('click', () => this.applyDraft());

      this.searchInput.addEventListener('input', () => {
        this.searchText = this.searchInput.value.trim().toLocaleLowerCase();
        this.renderList();
      });

      this.refreshValues(false);
      if (params.initialModel && Array.isArray(params.initialModel.values)) {
        this.setModel(params.initialModel);
      }
    }

    getGui() {
      return this.gui;
    }

    afterGuiAttached() {
      this.draftSelectedValues = new Set(this.selectedValues);
      this.renderList();
      window.setTimeout(() => this.searchInput?.focus(), 0);
    }

    isFilterActive() {
      return (
        this.selectedValues.size !== this.allValues.length ||
        this.allValues.some(value => !this.selectedValues.has(value)) ||
        Array.from(this.selectedValues).some(value => !this.allValues.includes(value))
      );
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
        values: Array.from(this.selectedValues),
      };
    }

    setModel(model) {
      if (!model || !Array.isArray(model.values)) {
        this.selectedValues = new Set(this.allValues);
      } else {
        this.selectedValues = new Set(model.values.map(value => this.normalize(value)));
      }
      this.draftSelectedValues = new Set(this.selectedValues);
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
      const configuredValues = Array.isArray(this.params.values)
        ? this.params.values
        : null;
      if (configuredValues) {
        configuredValues.forEach(value => values.add(this.normalize(value)));
      } else {
        this.params.api.forEachLeafNode(node => {
          if (
            this.params.showValuesFromOtherFilters !== false &&
            typeof this.params.doesRowPassOtherFilter === 'function' &&
            !this.params.doesRowPassOtherFilter(node)
          ) {
            return;
          }
          values.add(this.filterValue(node));
        });
      }

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
      const wasInactive =
        previousValues.size === previousSelection.size &&
        Array.from(previousValues).every(value => previousSelection.has(value));

      this.allValues = this.collectValues();

      if (!preserveSelection || wasInactive) {
        this.selectedValues = new Set(this.allValues);
      } else {
        // 保留当前数据集暂时不可见的选项，避免组合筛选或关键词变化时丢失选择。
        this.selectedValues = new Set(previousSelection);
      }

      this.draftSelectedValues = new Set(this.selectedValues);
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

    notifyFilterModified() {
      this.params.filterModifiedCallback?.();
    }

    applyDraft() {
      this.selectedValues = new Set(this.draftSelectedValues);
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
      const selectedVisibleCount = visible.filter(value => this.draftSelectedValues.has(value)).length;
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
          if (!this.searchText) {
            this.draftSelectedValues = new Set(this.allValues);
          } else {
            visible.forEach(value => this.draftSelectedValues.add(value));
          }
        } else {
          visible.forEach(value => this.draftSelectedValues.delete(value));
        }
        this.renderList();
        this.notifyFilterModified();
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

        const checkbox = this.createCheckbox(this.draftSelectedValues.has(value));
        const text = document.createElement('span');
        text.className = 'vpn-grid-set-filter__option-text';
        text.textContent = value;

        row.append(checkbox, text);
        checkbox.addEventListener('change', () => {
          if (checkbox.checked) {
            this.draftSelectedValues.add(value);
          } else {
            this.draftSelectedValues.delete(value);
          }
          this.renderList();
          this.notifyFilterModified();
        });

        this.list.append(row);
      });
    }
  }


  class NiceVpnMultiTextFilter {
    init(params) {
      this.params = params;
      this.values = [];
      this.draftValues = [];
      this.mode = 'containsAny';
      this.draftMode = 'containsAny';

      this.gui = document.createElement('div');
      this.gui.className = 'vpn-grid-multi-text-filter';

      this.modeSelect = document.createElement('select');
      this.modeSelect.className = 'vpn-grid-multi-text-filter__mode';
      [
        ['containsAny', '包含任一'],
        ['containsAll', '包含全部'],
        ['notContainsAny', '不包含'],
        ['equalsAny', '等于任一'],
      ].forEach(([value, label]) => {
        const option = document.createElement('option');
        option.value = value;
        option.textContent = label;
        this.modeSelect.append(option);
      });

      this.inputWrap = document.createElement('div');
      this.inputWrap.className = 'vpn-grid-multi-text-filter__input-wrap';

      this.tags = document.createElement('div');
      this.tags.className = 'vpn-grid-multi-text-filter__tags';

      this.input = document.createElement('input');
      this.input.type = 'text';
      this.input.autocomplete = 'off';
      this.input.spellcheck = false;
      this.input.placeholder = String(params.inputPlaceholder || '输入后按 Enter');

      this.actions = document.createElement('div');
      this.actions.className = 'vpn-grid-filter-actions';

      this.resetButton = document.createElement('button');
      this.resetButton.type = 'button';
      this.resetButton.className = 'vpn-grid-filter-actions__button';
      this.resetButton.textContent = '清除';

      this.applyButton = document.createElement('button');
      this.applyButton.type = 'button';
      this.applyButton.className = 'vpn-grid-filter-actions__button vpn-grid-filter-actions__button--primary';
      this.applyButton.textContent = '应用';

      this.actions.append(this.resetButton, this.applyButton);
      this.inputWrap.append(this.tags, this.input);
      this.gui.append(this.modeSelect, this.inputWrap, this.actions);

      this.modeSelect.addEventListener('change', () => {
        this.draftMode = this.modeSelect.value || 'containsAny';
        this.notifyFilterModified();
      });
      this.resetButton.addEventListener('click', () => {
        this.draftValues = [];
        this.draftMode = 'containsAny';
        this.modeSelect.value = this.draftMode;
        this.input.value = '';
        this.renderTags();
        this.notifyFilterModified();
      });
      this.applyButton.addEventListener('click', () => this.applyDraft());

      this.input.addEventListener('keydown', event => {
        if (event.key === 'Enter' || event.key === ',') {
          event.preventDefault();
          this.commitInput();
          return;
        }
        if (event.key === 'Backspace' && !this.input.value && this.draftValues.length) {
          this.draftValues.pop();
          this.renderTags();
          this.notifyFilterModified();
        }
      });

      this.input.addEventListener('paste', event => {
        const text = event.clipboardData?.getData('text') || '';
        if (!/[\n,]/.test(text)) {
          return;
        }
        event.preventDefault();
        this.addValues(text.split(/[\n,]+/));
      });

      if (params.initialModel && Array.isArray(params.initialModel.values)) {
        this.setModel(params.initialModel);
      }
    }

    getGui() {
      return this.gui;
    }

    afterGuiAttached() {
      this.draftValues = [...this.values];
      this.draftMode = this.mode;
      this.modeSelect.value = this.draftMode;
      this.renderTags();
      window.setTimeout(() => this.input?.focus(), 0);
    }

    isFilterActive() {
      return this.values.length > 0;
    }

    normalizeValue(value) {
      return String(value ?? '').trim();
    }

    addValues(rawValues) {
      const next = [...this.draftValues];
      const seen = new Set(next.map(value => value.toLocaleLowerCase()));
      rawValues.forEach(raw => {
        const value = this.normalizeValue(raw);
        const key = value.toLocaleLowerCase();
        if (!value || seen.has(key)) {
          return;
        }
        seen.add(key);
        next.push(value);
      });
      if (next.length === this.draftValues.length) {
        return;
      }
      this.draftValues = next;
      this.input.value = '';
      this.renderTags();
      this.notifyFilterModified();
    }

    commitInput() {
      this.addValues([this.input.value]);
    }

    removeValue(index) {
      if (index < 0 || index >= this.draftValues.length) {
        return;
      }
      this.draftValues.splice(index, 1);
      this.renderTags();
      this.notifyFilterModified();
    }

    renderTags() {
      this.tags.replaceChildren();
      this.draftValues.forEach((value, index) => {
        const tag = document.createElement('span');
        tag.className = 'vpn-grid-multi-text-filter__tag';

        const text = document.createElement('span');
        text.className = 'vpn-grid-multi-text-filter__tag-text';
        text.textContent = value;

        const remove = document.createElement('button');
        remove.type = 'button';
        remove.className = 'vpn-grid-multi-text-filter__tag-remove';
        remove.setAttribute('aria-label', '删除 ' + value);
        remove.textContent = '×';
        remove.addEventListener('click', () => this.removeValue(index));

        tag.append(text, remove);
        this.tags.append(tag);
      });
    }

    textValue(node) {
      const field = String(this.params.filterValueField || '').trim();
      const value = field ? node?.data?.[field] : this.params.getValue(node);
      return String(value ?? '').toLocaleLowerCase();
    }

    doesFilterPass(params) {
      if (!this.isFilterActive()) {
        return true;
      }
      const actual = this.textValue(params.node);
      const needles = this.values.map(value => value.toLocaleLowerCase());

      if (this.mode === 'containsAll') {
        return needles.every(value => actual.includes(value));
      }
      if (this.mode === 'notContainsAny') {
        return needles.every(value => !actual.includes(value));
      }
      if (this.mode === 'equalsAny') {
        return needles.some(value => actual === value);
      }
      return needles.some(value => actual.includes(value));
    }

    getModel() {
      if (!this.isFilterActive()) {
        return null;
      }
      return {
        filterType: 'niceVpnMultiText',
        type: this.mode,
        values: [...this.values],
      };
    }

    setModel(model) {
      if (!model || !Array.isArray(model.values)) {
        this.values = [];
        this.mode = 'containsAny';
      } else {
        this.values = model.values
          .map(value => this.normalizeValue(value))
          .filter(Boolean);
        this.mode = String(model.type || 'containsAny');
      }
      this.draftValues = [...this.values];
      this.draftMode = this.mode;
      this.modeSelect.value = this.draftMode;
      this.input.value = '';
      this.renderTags();
    }

    getModelAsString() {
      if (!this.isFilterActive()) {
        return '';
      }
      const labels = {
        containsAny: '包含任一',
        containsAll: '包含全部',
        notContainsAny: '不包含',
        equalsAny: '等于任一',
      };
      return (labels[this.mode] || '包含任一') + ': ' + this.values.join(', ');
    }

    notifyFilterModified() {
      this.params.filterModifiedCallback?.();
    }

    applyDraft() {
      this.commitInput();
      this.values = [...this.draftValues];
      this.mode = this.draftMode;
      this.params.filterModifiedCallback?.();
      this.params.filterChangedCallback?.();
    }
  }

  window.NiceVpnSetFilter = NiceVpnSetFilter;
  window.NiceVpnMultiTextFilter = NiceVpnMultiTextFilter;
})();
