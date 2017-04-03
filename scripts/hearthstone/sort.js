var sort = {

  sortStates : {},

  sortStatesDefault : {
    "name" : "asc",
    "cardClass" : "desc",
    "type" : "desc" ,
    "rarity" : "desc",
    "set" : "desc",
    "cost" : "asc"
  },

  resetSort : function(){
    sort.sortStates = jQuery.extend(true, {}, sort.sortStatesDefault);
  },

  sortTable : function(parameter){
    sort.changeHeader(parameter);
    parameter = parameter.slice(8)

    if (sort.sortStates[parameter] == "asc") {
      sort.sortStates[parameter] = "desc";
      sort.sortData(parameter, "desc");
    } else if (sort.sortStates[parameter] == "desc") {
      sort.sortStates[parameter] = "asc";
      sort.sortData(parameter, "asc");
    }

    filters.writeTable();

  },

  changeHeader : function(parameter){
    $(".sort-active").removeClass();
    $("#" + parameter).addClass("sort-active")
  },

  sortData : function(parameter, sorttype){

    var sortArray = filters.filteredList.map(function(data, ind){
      return {ind:ind, data:data}
    });

    // sort works both alphabetically and numerically
    if (sorttype == "asc"){
      sortArray.sort(function(a, b){
        if (a.data[parameter] < b.data[parameter]){
          return -1;
        }
        if (a.data[parameter] > b.data[parameter]){
          return 1;
        }
        return a.ind - b.ind;
      })
    }
    if (sorttype == "desc"){
      sortArray.sort(function(a, b){
        if (a.data[parameter] < b.data[parameter]){
          return 1;
        }
        if (a.data[parameter] > b.data[parameter]){
          return -1;
        }
        return a.ind - b.ind;
      })
    }


    filters.filteredList = sortArray.map(function(val){
      return val.data;
    });

  }
}
