var filter = {
  pollsDisplay : true,
  modelDisplay : true,
  movingDisplay: true,

  datestart : new Date(2015, 4, 6),
  dateend : new Date(2017, 5, 8),

  date : function(value, id){

    if (id == "dateselect-start"){
      filter.datestart = new Date(value);
    }

    if (id == "dateselect-end"){
      filter.dateend = new Date(value);
    }

    var defStart = new Date(2015, 4, 6);
    var defEnd =  new Date(2017, 5, 8);

    if (filter.datestart <= defStart || filter.datestart == "Invalid Date"){
      filter.datestart = defStart;
    }

    if (filter.dateend >= defEnd || filter.dateend == "Invalid Date"){
      filter.dateend = defEnd;
    }

    filter.redraw();
  },

  company : function(value){
    if (companies.indexOf(value) == -1){
      companies.push(value);
    } else {
      var i = companies.indexOf(value);
      companies.splice(i, 1)
    }

    filter.redraw();
  },

  redraw: function(){
    $("#plot g").empty()
    pageLoad();
  }
}
